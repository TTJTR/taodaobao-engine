import assert from "node:assert/strict";
import { test } from "node:test";

import type { RuntimeJob } from "./contracts.js";
import { executeJob, type EngineEvent } from "./engine.js";

const credentials = {
  brightDataApiKey: "test-bright-data",
  brightDataSerpZone: "test-serp",
  brightDataUnlockerZone: "test-unlocker",
  openRouterApiKey: "test-openrouter",
};

function createJob(maxToolCalls = 3, maxCostUsd = 0.1): RuntimeJob {
  return {
    id: crypto.randomUUID(),
    request: {
      client_job_id: crypto.randomUUID(),
      company_name: "Example",
      allowed_fields: ["industry"],
      language: "zh-CN",
      country: "CN",
      max_tool_calls: maxToolCalls,
      max_cost_usd: maxCostUsd,
    },
    status: "queued",
    stage: "queued",
    retryable: false,
    toolCallsUsed: 0,
    costUsd: 0,
    controller: new AbortController(),
    createdAt: new Date().toISOString(),
  };
}

function eventStream(events: EngineEvent[], consumed: EngineEvent[]): () => AsyncIterable<EngineEvent> {
  return async function* () {
    for (const event of events) {
      consumed.push(event);
      yield event;
    }
  };
}

test("aborts at the tool call limit without consuming the next event", async () => {
  const job = createJob(3, 1);
  const consumed: EngineEvent[] = [];
  const events: EngineEvent[] = [
    { type: "cost_update", cost: { totalCost: 0.01, toolCalls: 1 } },
    { type: "cost_update", cost: { totalCost: 0.02, toolCalls: 2 } },
    { type: "cost_update", cost: { totalCost: 0.03, toolCalls: 3 } },
    { type: "cost_update", cost: { totalCost: 0.04, toolCalls: 4 } },
  ];

  await executeJob(job, { credentials, runEnrichment: eventStream(events, consumed) });

  assert.equal(job.controller.signal.aborted, true);
  assert.equal(consumed.length, 3);
  assert.equal(job.status, "failed");
  assert.equal(job.errorCode, "TOOL_CALL_LIMIT_EXCEEDED");
  assert.equal(job.toolCallsUsed, 3);
  assert.equal(job.costUsd, 0.03);
});

test("aborts when reported cost reaches the budget", async () => {
  const job = createJob(10, 0.1);
  const consumed: EngineEvent[] = [];
  const events: EngineEvent[] = [
    { type: "cost_update", cost: { totalCost: 0.05, toolCalls: 1 } },
    { type: "cost_update", cost: { totalCost: 0.1, toolCalls: 2 } },
    { type: "complete", successCount: 1, totalRows: 1 },
  ];

  await executeJob(job, { credentials, runEnrichment: eventStream(events, consumed) });

  assert.equal(job.controller.signal.aborted, true);
  assert.equal(consumed.length, 2);
  assert.equal(job.status, "failed");
  assert.equal(job.errorCode, "QUOTA_EXCEEDED");
  assert.equal(job.toolCallsUsed, 2);
  assert.equal(job.costUsd, 0.1);
});
