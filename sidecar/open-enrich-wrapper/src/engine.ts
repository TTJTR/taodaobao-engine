import type { EnrichmentRequest, Fact, FactCategory, RuntimeJob } from "./contracts.js";

interface Source { url: string; snippet: string; confidence: number }
interface Enrichment {
  field: string;
  value: string | number | boolean | string[] | null;
  confidence: number;
  source?: string;
  sourceContext?: Source[];
}
interface RowResult { status: string; error?: string; enrichments: Record<string, Enrichment> }
interface Cost { totalCost: number; toolCalls: number }
export type EngineEvent =
  | { type: "session"; sessionId: string }
  | { type: "pending"; rowIndex: number; totalRows: number }
  | { type: "processing"; rowIndex: number; totalRows: number }
  | { type: "agent_progress"; step: string }
  | { type: "cost_update"; cost: Cost }
  | { type: "result"; result: RowResult; cost?: Cost }
  | { type: "error"; error: string }
  | { type: "complete"; successCount: number; totalRows: number };
type RunEnrichment = (options: Record<string, unknown>) => AsyncIterable<EngineEvent>;
export interface ExecuteJobOptions {
  runEnrichment?: RunEnrichment;
  credentials?: Record<string, string>;
  onUpdate?: (job: RuntimeJob) => Promise<void>;
}

export async function executeJob(job: RuntimeJob, options: ExecuteJobOptions = {}): Promise<void> {
  const credentials = options.credentials ?? credentialsFromEnvironment();
  const runEnrichment = options.runEnrichment ?? await loadRunEnrichment();

  if (isTerminal(job.status)) return;
  job.status = "processing";
  job.stage = "discovery";
  await options.onUpdate?.(job);
  let rowResult: RowResult | undefined;
  const fields = job.request.allowed_fields.map((field) => ({
    name: field,
    displayName: field.replaceAll("_", " "),
    description: `Research the public, citable value for ${field}`,
    type: "string",
    required: false,
    category: "custom",
  }));
  const rows = [{
    companyname: job.request.company_name,
    companywebsite: job.request.website_url ?? "",
  }];

  for await (const event of runEnrichment({
    rows,
    fields,
    identifierColumn: job.request.website_url ? "companywebsite" : "companyname",
    credentials,
    concurrency: 1,
    maxRows: 1,
    maxFields: job.request.allowed_fields.length,
    signal: job.controller.signal,
  })) {
    if (isTerminal(job.status) || job.controller.signal.aborted) return;
    if (event.type === "agent_progress") {
      job.stage = event.step;
      await options.onUpdate?.(job);
    }
    if (event.type === "cost_update") {
      updateCost(job, event.cost);
      if (enforceLimits(job)) {
        await options.onUpdate?.(job);
        return;
      }
      await options.onUpdate?.(job);
    }
    if (event.type === "result") {
      rowResult = event.result;
      if (event.cost) {
        updateCost(job, event.cost);
        if (enforceLimits(job)) {
          await options.onUpdate?.(job);
          return;
        }
        await options.onUpdate?.(job);
      }
    }
    if (event.type === "error") throw providerError(classifyError(event.error), safeSummary(event.error));
  }

  if (!rowResult || rowResult.status !== "completed") {
    throw providerError("ENRICHMENT_FAILED", safeSummary(rowResult?.error ?? "No result returned"));
  }
  const facts = Object.values(rowResult.enrichments).flatMap(toFact);
  if (facts.length === 0) throw providerError("NO_CITABLE_FACTS", "No facts with citations returned");
  job.result = {
    provider_job_id: job.id,
    status: "completed",
    facts,
    warnings: [],
    tool_calls_used: job.toolCallsUsed,
    cost_usd: job.costUsd,
    provider_metadata: { engine: "open-enrich", language: job.request.language },
  };
  transitionTerminal(job, "completed", "completed");
  await options.onUpdate?.(job);
}

async function loadRunEnrichment(): Promise<RunEnrichment> {
  const moduleName = process.env.OPEN_ENRICH_CORE_MODULE ?? "@brightdata/enrich-core";
  const core = await import(moduleName) as { runEnrichment?: RunEnrichment };
  if (!core.runEnrichment) throw providerError("ENGINE_UNAVAILABLE", "runEnrichment is unavailable");
  return core.runEnrichment;
}

function toFact(item: Enrichment): Fact[] {
  if (item.value === null) return [];
  const sources = item.sourceContext ?? (item.source ? [{ url: item.source, snippet: "", confidence: item.confidence }] : []);
  const citations = sources
    .filter((source) => source.url && source.snippet.trim())
    .map((source) => ({
      url: source.url,
      quote: source.snippet.trim(),
      provider_confidence: clamp(source.confidence),
    }));
  if (citations.length === 0) return [];
  return [{
    field: item.field,
    value: item.value,
    category: categoryFor(item.field),
    provider_confidence: clamp(item.confidence),
    citations,
  }];
}

function credentialsFromEnvironment() {
  const credentials = {
    brightDataApiKey: process.env.BRIGHT_DATA_API_KEY,
    brightDataSerpZone: process.env.BRIGHT_DATA_SERP_ZONE,
    brightDataUnlockerZone: process.env.BRIGHT_DATA_UNLOCKER_ZONE,
    openRouterApiKey: process.env.OPENROUTER_API_KEY,
  };
  const missing = Object.entries(credentials).filter(([, value]) => !value).map(([key]) => key);
  if (missing.length) throw providerError("PROVIDER_CONFIG_INVALID", `Missing required credentials: ${missing.join(", ")}`);
  return credentials as Record<string, string>;
}

function updateCost(job: RuntimeJob, cost: Cost): void {
  job.costUsd = Math.max(job.costUsd, cost.totalCost);
  job.toolCallsUsed = Math.max(job.toolCallsUsed, cost.toolCalls);
}

function enforceLimits(job: RuntimeJob): boolean {
  let errorCode: string | undefined;
  if (job.toolCallsUsed >= job.request.max_tool_calls) errorCode = "TOOL_CALL_LIMIT_EXCEEDED";
  else if (job.costUsd >= job.request.max_cost_usd) errorCode = "QUOTA_EXCEEDED";
  if (!errorCode) return false;

  if (transitionTerminal(job, "failed", "failed", errorCode, "Provider execution limit reached")) {
    job.controller.abort();
  }
  return true;
}

export function isTerminal(status: RuntimeJob["status"]): boolean {
  return status === "completed" || status === "partial" || status === "failed";
}

export function transitionTerminal(
  job: RuntimeJob,
  status: "completed" | "partial" | "failed",
  stage: string,
  errorCode?: string,
  errorSummary?: string,
): boolean {
  if (isTerminal(job.status)) return false;
  job.status = status;
  job.stage = stage;
  job.errorCode = errorCode;
  job.errorSummary = errorSummary;
  return true;
}

function categoryFor(field: string): FactCategory {
  if (/hire|job|recruit/i.test(field)) return "hiring_signal";
  if (/tech|stack|software/i.test(field)) return "technology_signal";
  if (/project|tender|rfp/i.test(field)) return "project_signal";
  if (/risk|legal|lawsuit/i.test(field)) return "risk_signal";
  if (/compet/i.test(field)) return "competitive_signal";
  if (/fund|buy|signal|change/i.test(field)) return "buying_signal";
  return "company_profile";
}

function classifyError(message: string): string {
  if (/quota|credit|billing|payment|insufficient/i.test(message)) return "PROVIDER_QUOTA_EXHAUSTED";
  if (/unauthorized|forbidden|api.?key/i.test(message)) return "PROVIDER_AUTH_FAILED";
  if (/timeout/i.test(message)) return "PROVIDER_TIMEOUT";
  return "ENRICHMENT_FAILED";
}

export function safeSummary(message: string): string {
  let sanitized = message
    .replace(/(?:authorization\s*[:=]\s*)?bearer\s+[^\s,;]+/gi, "Authorization: [REDACTED]")
    .replace(/(?:sk-|brd_)[A-Za-z0-9_-]{8,}/g, "[REDACTED]");
  for (const secret of [process.env.BRIGHT_DATA_API_KEY, process.env.OPENROUTER_API_KEY]) {
    if (secret) sanitized = sanitized.replaceAll(secret, "[REDACTED]");
  }
  return sanitized.slice(0, 1000);
}

function clamp(value: number): number { return Math.max(0, Math.min(1, value)); }

function providerError(code: string, message: string): Error & { code: string } {
  return Object.assign(new Error(message), { code });
}
