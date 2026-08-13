import assert from "node:assert/strict";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

import type { JobRecord } from "./contracts.js";
import type { JobStore } from "./job-store.js";

process.env.NODE_ENV = "test";
const { createApp } = await import("./server.js");
const token = "test-internal-token";

class FakeJobStore implements JobStore {
  records = new Map<string, JobRecord>();
  cancelled: string[] = [];
  pingFails = false;
  cancelHandler: ((jobId: string) => void) | undefined;
  async connect(): Promise<void> {}
  async get(jobId: string): Promise<JobRecord | null> { return this.records.get(jobId) ?? null; }
  async set(job: JobRecord): Promise<boolean> {
    const current = this.records.get(job.id);
    if (current && ["completed", "partial", "failed"].includes(current.status)) return false;
    this.records.set(job.id, structuredClone(job));
    return true;
  }
  async publishCancel(jobId: string): Promise<void> {
    this.cancelled.push(jobId);
    this.cancelHandler?.(jobId);
  }
  async subscribeCancel(handler: (jobId: string) => void): Promise<void> { this.cancelHandler = handler; }
  async ping(): Promise<void> { if (this.pingFails) throw new Error("unavailable"); }
  async close(): Promise<void> {}
}

const store = new FakeJobStore();
const runtime = createApp(store, token);
let server: Server;
let baseUrl: string;
const auth = { authorization: `Bearer ${token}` };

before(async () => {
  server = runtime.app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => server.once("listening", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

test("health verifies the backing store without exposing details", async () => {
  const healthy = await fetch(`${baseUrl}/healthz`);
  assert.equal(healthy.status, 200);
  assert.deepEqual(await healthy.json(), { status: "ok" });
  assert.equal(healthy.headers.get("x-powered-by"), null);

  store.pingFails = true;
  const unavailable = await fetch(`${baseUrl}/healthz`);
  assert.equal(unavailable.status, 503);
  assert.deepEqual(await unavailable.json(), { status: "unavailable" });
  store.pingFails = false;
});

test("internal routes reject missing and invalid bearer tokens", async () => {
  const missing = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/unknown`);
  const invalid = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/unknown`, {
    headers: { authorization: "Bearer wrong-token" },
  });

  assert.equal(missing.status, 401);
  assert.equal(invalid.status, 401);
});

test("reads persisted job state and publishes cross-replica cancellation", async () => {
  const jobId = crypto.randomUUID();
  await store.set({
    id: jobId,
    request: {
      client_job_id: crypto.randomUUID(), company_name: "Example", allowed_fields: ["industry"],
      language: "zh-CN", country: "CN", max_tool_calls: 3, max_cost_usd: 0.1,
    },
    status: "processing", stage: "discovery", retryable: false,
    toolCallsUsed: 1, costUsd: 0.01, createdAt: new Date().toISOString(),
  });

  const status = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/${jobId}`, { headers: auth });
  assert.equal(status.status, 200);
  assert.equal((await status.json() as { tool_calls_used: number }).tool_calls_used, 1);

  const cancelled = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/${jobId}`, {
    method: "DELETE", headers: auth,
  });
  assert.equal(cancelled.status, 200);
  assert.deepEqual(store.cancelled, [jobId]);
  assert.equal((await store.get(jobId))?.errorCode, "CANCELLED");
});

test("rejects unknown request properties after authentication", async () => {
  const response = await fetch(`${baseUrl}/internal/v1/enrichment-jobs`, {
    method: "POST",
    headers: { ...auth, "content-type": "application/json" },
    body: JSON.stringify({ company_name: "Example", unexpected: "value" }),
  });
  assert.equal(response.status, 422);
});

test("DELETE cannot overwrite a completed persisted terminal state", async () => {
  const jobId = crypto.randomUUID();
  await store.set({
    id: jobId,
    request: {
      client_job_id: crypto.randomUUID(), company_name: "Completed Example",
      allowed_fields: ["industry"], language: "zh-CN", country: "CN",
      max_tool_calls: 3, max_cost_usd: 0.1,
    },
    status: "completed", stage: "completed", retryable: false,
    toolCallsUsed: 2, costUsd: 0.01, createdAt: new Date().toISOString(),
  });

  const response = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/${jobId}`, {
    method: "DELETE", headers: auth,
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { status: string }).status, "completed");
  assert.equal((await store.get(jobId))?.status, "completed");
});
