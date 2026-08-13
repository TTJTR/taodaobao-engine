import { z } from "zod";

export const requestSchema = z.object({
  client_job_id: z.string().uuid(),
  company_name: z.string().min(1).max(500),
  website_url: z.url().nullable().optional(),
  allowed_fields: z.array(z.string().regex(/^[a-z][a-z0-9_]{0,127}$/)).min(1).max(20),
  language: z.string().min(2).max(16).default("zh-CN"),
  country: z.string().length(2).nullable().optional().default("CN"),
  max_tool_calls: z.number().int().min(1).max(200).default(50),
  max_cost_usd: z.number().positive().max(100).default(2),
}).strict().refine((value) => new Set(value.allowed_fields).size === value.allowed_fields.length, {
  message: "allowed_fields must be unique",
});

export type EnrichmentRequest = z.infer<typeof requestSchema>;
export type JobStatus = "queued" | "processing" | "completed" | "partial" | "failed";
export type FactCategory =
  | "company_profile"
  | "buying_signal"
  | "hiring_signal"
  | "technology_signal"
  | "project_signal"
  | "risk_signal"
  | "competitive_signal";

export interface Citation {
  url: string;
  quote: string;
  provider_confidence?: number;
}

export interface Fact {
  field: string;
  value: string | number | boolean | string[];
  category: FactCategory;
  provider_confidence: number;
  citations: Citation[];
}

export interface JobResult {
  provider_job_id: string;
  status: "completed" | "partial";
  facts: Fact[];
  warnings: string[];
  tool_calls_used: number;
  cost_usd: number;
  provider_metadata: Record<string, unknown>;
}

export interface JobRecord {
  id: string;
  request: EnrichmentRequest;
  status: JobStatus;
  stage: string;
  retryable: boolean;
  errorCode?: string;
  errorSummary?: string;
  toolCallsUsed: number;
  costUsd: number;
  result?: JobResult;
  createdAt: string;
}

export interface RuntimeJob extends JobRecord {
  controller: AbortController;
}
