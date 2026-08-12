import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

process.env.NODE_ENV = "test";
const { app } = await import("./server.js");
let server: Server;
let baseUrl: string;

before(async () => {
  server = app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => server.once("listening", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
});

test("submit, inspect and cancel a job using the Python contract", async () => {
  const submitted = await fetch(`${baseUrl}/internal/v1/enrichment-jobs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_job_id: crypto.randomUUID(),
      company_name: "Example",
      website_url: "https://example.com",
      allowed_fields: ["industry"],
      language: "zh-CN",
      country: "CN",
      max_tool_calls: 10,
      max_cost_usd: 2,
    }),
  });
  assert.equal(submitted.status, 202);
  const accepted = await submitted.json() as { provider_job_id: string; status: string; accepted_at: string };
  assert.equal(accepted.status, "queued");
  assert.ok(accepted.accepted_at);

  const statusResponse = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/${accepted.provider_job_id}`);
  const status = await statusResponse.json() as Record<string, unknown>;
  assert.equal(statusResponse.status, 200);
  assert.equal(status.provider_job_id, accepted.provider_job_id);
  assert.ok(["queued", "processing", "failed"].includes(String(status.status)));
  assert.equal(typeof status.cost_usd, "number");
  assert.equal(typeof status.tool_calls_used, "number");

  const cancelled = await fetch(`${baseUrl}/internal/v1/enrichment-jobs/${accepted.provider_job_id}`, {
    method: "DELETE",
  });
  assert.equal(cancelled.status, 200);
});

test("rejects unknown request properties", async () => {
  const response = await fetch(`${baseUrl}/internal/v1/enrichment-jobs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ company_name: "Example", unexpected: "value" }),
  });
  assert.equal(response.status, 422);
});
