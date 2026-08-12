import crypto from "node:crypto";
import express from "express";
import { ZodError } from "zod";

import { requestSchema, type JobRecord } from "./contracts.js";
import { executeJob, safeSummary } from "./engine.js";

const app = express();
const jobs = new Map<string, JobRecord>();
const port = Number.parseInt(process.env.PORT ?? "8090", 10);
const host = process.env.HOST ?? "0.0.0.0";

app.disable("x-powered-by");
app.use(express.json({ limit: "64kb" }));

app.get("/healthz", (_request, response) => response.json({ status: "ok" }));

app.post("/internal/v1/enrichment-jobs", (request, response, next) => {
  try {
    const input = requestSchema.parse(request.body);
    const id = crypto.randomUUID();
    const job: JobRecord = {
      id,
      request: input,
      status: "queued",
      stage: "queued",
      retryable: false,
      toolCallsUsed: 0,
      costUsd: 0,
      controller: new AbortController(),
      createdAt: new Date().toISOString(),
    };
    jobs.set(id, job);
    void executeJob(job).catch((error: unknown) => failJob(job, error));
    response.status(202).json({ provider_job_id: id, status: "queued", accepted_at: job.createdAt });
  } catch (error) {
    next(error);
  }
});

app.get("/internal/v1/enrichment-jobs/:jobId", (request, response) => {
  const job = jobs.get(request.params.jobId);
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
});

app.get("/internal/v1/enrichment-jobs/:jobId/results", (request, response) => {
  const job = jobs.get(request.params.jobId);
  if (!job) return response.status(404).json({ error_code: "JOB_NOT_FOUND" });
  if (!job.result) return response.status(409).json({ error_code: "RESULT_NOT_READY" });
  return response.json(job.result);
});

app.delete("/internal/v1/enrichment-jobs/:jobId", (request, response) => {
  const job = jobs.get(request.params.jobId);
  if (!job) return response.status(404).json({ error_code: "JOB_NOT_FOUND" });
  if (["queued", "processing"].includes(job.status)) {
    job.controller.abort();
    job.status = "failed";
    job.stage = "cancelled";
    job.errorCode = "CANCELLED";
    job.errorSummary = "Cancelled by caller";
  }
  return response.json({ provider_job_id: job.id, status: job.status });
});

app.use((error: unknown, _request: express.Request, response: express.Response, _next: express.NextFunction) => {
  if (error instanceof ZodError) {
    return response.status(422).json({ error_code: "VALIDATION_FAILED", issues: error.issues });
  }
  console.error("request failed", error instanceof Error ? error.name : "UnknownError");
  return response.status(500).json({ error_code: "INTERNAL_ERROR" });
});

function failJob(job: JobRecord, error: unknown): void {
  if (job.controller.signal.aborted) return;
  const candidate = error as { code?: unknown; message?: unknown };
  job.status = "failed";
  job.stage = "failed";
  job.errorCode = typeof candidate.code === "string" ? candidate.code.slice(0, 64) : "ENRICHMENT_FAILED";
  job.errorSummary = safeSummary(typeof candidate.message === "string" ? candidate.message : "Open Enrich failed");
  job.retryable = ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"].includes(job.errorCode);
  console.error("enrichment job failed", { jobId: job.id, errorCode: job.errorCode });
}

if (process.env.NODE_ENV !== "test") {
  app.listen(port, host, () => console.log(`open-enrich-wrapper listening on ${host}:${port}`));
}

export { app, jobs };
