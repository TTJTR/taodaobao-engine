import crypto from "node:crypto";
import { timingSafeEqual } from "node:crypto";
import type { Server } from "node:http";

import express from "express";
import { ZodError } from "zod";

import { requestSchema, type JobRecord, type RuntimeJob } from "./contracts.js";
import { executeJob, isTerminal, safeSummary, transitionTerminal } from "./engine.js";
import { RedisJobStore, type JobStore } from "./job-store.js";

const port = Number.parseInt(process.env.PORT ?? "8090", 10);
const host = process.env.HOST ?? "0.0.0.0";

export function createApp(store: JobStore, token: string) {
  const app = express();
  const controllers = new Map<string, AbortController>();
  let acceptingRequests = true;

  app.disable("x-powered-by");
  app.use(express.json({ limit: "64kb" }));

  app.get("/healthz", async (_request, response) => {
    if (!acceptingRequests) return response.status(503).json({ status: "unavailable" });
    try {
      await store.ping();
      return response.json({ status: "ok" });
    } catch {
      return response.status(503).json({ status: "unavailable" });
    }
  });

  app.use("/internal/v1", (request, response, next) => {
    if (!acceptingRequests) return response.status(503).json({ error_code: "SHUTTING_DOWN" });
    const authorization = request.header("authorization") ?? "";
    if (!validBearerToken(authorization, token)) {
      return response.status(401).json({ error_code: "UNAUTHORIZED" });
    }
    next();
  });

  app.post("/internal/v1/enrichment-jobs", async (request, response, next) => {
    try {
      const input = requestSchema.parse(request.body);
      const id = crypto.randomUUID();
      const controller = new AbortController();
      const job: RuntimeJob = {
        id,
        request: input,
        status: "queued",
        stage: "queued",
        retryable: false,
        toolCallsUsed: 0,
        costUsd: 0,
        controller,
        createdAt: new Date().toISOString(),
      };
      controllers.set(id, controller);
      await store.set(persistable(job));
      void executeJob(job, {
        onUpdate: async (updated) => {
          await store.set(persistable(updated));
        },
      })
        .catch(async (error: unknown) => failJob(job, error, store))
        .finally(() => controllers.delete(id));
      response.status(202).json({ provider_job_id: id, status: "queued", accepted_at: job.createdAt });
    } catch (error) {
      next(error);
    }
  });

  app.get("/internal/v1/enrichment-jobs/:jobId", async (request, response, next) => {
    try {
      const job = await store.get(request.params.jobId);
      if (!job) return response.status(404).json({ error_code: "JOB_NOT_FOUND" });
      return response.json({
        provider_job_id: job.id,
        status: job.status,
        stage: job.stage,
        retryable: job.retryable,
        error_code: job.errorCode ?? null,
        error_summary: job.errorSummary ?? null,
        tool_calls_used: job.toolCallsUsed,
        cost_usd: job.costUsd,
      });
    } catch (error) { next(error); }
  });

  app.get("/internal/v1/enrichment-jobs/:jobId/results", async (request, response, next) => {
    try {
      const job = await store.get(request.params.jobId);
      if (!job) return response.status(404).json({ error_code: "JOB_NOT_FOUND" });
      if (!job.result) return response.status(409).json({ error_code: "RESULT_NOT_READY" });
      return response.json(job.result);
    } catch (error) { next(error); }
  });

  app.delete("/internal/v1/enrichment-jobs/:jobId", async (request, response, next) => {
    try {
      const job = await store.get(request.params.jobId);
      if (!job) return response.status(404).json({ error_code: "JOB_NOT_FOUND" });
      if (!isTerminal(job.status)) {
        job.status = "failed";
        job.stage = "cancelled";
        job.errorCode = "CANCELLED";
        job.errorSummary = "Cancelled by caller";
        await store.set(job);
        await store.publishCancel(job.id);
      }
      const persisted = await store.get(job.id);
      return response.json({ provider_job_id: job.id, status: persisted?.status ?? job.status });
    } catch (error) { next(error); }
  });

  app.use((error: unknown, _request: express.Request, response: express.Response, _next: express.NextFunction) => {
    if (error instanceof ZodError) {
      return response.status(422).json({ error_code: "VALIDATION_FAILED", issues: error.issues });
    }
    console.error("request failed", error instanceof Error ? error.name : "UnknownError");
    return response.status(500).json({ error_code: "INTERNAL_ERROR" });
  });

  return {
    app,
    beginShutdown: () => {
      acceptingRequests = false;
      for (const controller of controllers.values()) controller.abort();
    },
    cancelLocal: (jobId: string) => controllers.get(jobId)?.abort(),
  };
}

function validBearerToken(authorization: string, token: string): boolean {
  if (!token || !authorization.startsWith("Bearer ")) return false;
  const provided = Buffer.from(authorization.slice(7));
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

function persistable(job: RuntimeJob): JobRecord {
  const { controller: _controller, ...record } = job;
  return record;
}

async function failJob(job: RuntimeJob, error: unknown, store: JobStore): Promise<void> {
  if (isTerminal(job.status)) return;
  const candidate = error as { code?: unknown; message?: unknown };
  const errorCode = typeof candidate.code === "string" ? candidate.code.slice(0, 64) : "ENRICHMENT_FAILED";
  const errorSummary = safeSummary(typeof candidate.message === "string" ? candidate.message : "Open Enrich failed");
  transitionTerminal(job, "failed", "failed", errorCode, errorSummary);
  job.retryable = ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"].includes(errorCode);
  await store.set(persistable(job));
  console.error("enrichment job failed", { jobId: job.id, errorCode });
}

async function start(): Promise<void> {
  const redisUrl = process.env.REDIS_URL;
  const token = process.env.APP_OPEN_ENRICH_SVC_TOKEN ?? "";
  if (!redisUrl || !token) throw new Error("REDIS_URL and APP_OPEN_ENRICH_SVC_TOKEN are required");
  const store = new RedisJobStore(redisUrl, Number.parseInt(process.env.JOB_TTL_SECONDS ?? "86400", 10));
  await store.connect();
  const runtime = createApp(store, token);
  await store.subscribeCancel(runtime.cancelLocal);
  const server = runtime.app.listen(port, host, () => console.log(`open-enrich-wrapper listening on ${host}:${port}`));
  const shutdown = (signal: string) => void gracefulShutdown(signal, server, store, runtime.beginShutdown);
  process.once("SIGTERM", () => shutdown("SIGTERM"));
  process.once("SIGINT", () => shutdown("SIGINT"));
}

async function gracefulShutdown(signal: string, server: Server, store: JobStore, beginShutdown: () => void): Promise<void> {
  beginShutdown();
  console.log("open-enrich-wrapper shutting down", { signal });
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await store.close();
}

if (process.env.NODE_ENV !== "test") {
  void start().catch((error: unknown) => {
    console.error("open-enrich-wrapper startup failed", error instanceof Error ? error.name : "UnknownError");
    process.exitCode = 1;
  });
}
