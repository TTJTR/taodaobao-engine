# Open Enrich Wrapper

Internal, headless HTTP wrapper around Bright Data Open Enrich. It runs only the
`@brightdata/enrich-core` engine: no Next.js UI, email delivery, CLI, or MCP server.

## Contract

- `POST /internal/v1/enrichment-jobs` returns HTTP 202 with `provider_job_id`,
  `status=queued`, and `accepted_at`.
- `GET /internal/v1/enrichment-jobs/:jobId` returns status, stage, error fields,
  `cost_usd`, and `tool_calls_used`.
- `GET /internal/v1/enrichment-jobs/:jobId/results` returns Python's strict
  `EnrichmentJobResult`: facts use `provider_confidence` and `citations` containing
  source `url` and verbatim `quote`.
- `DELETE /internal/v1/enrichment-jobs/:jobId` aborts the Open Enrich run.

The wrapper deliberately drops facts without both a URL and a non-empty source
snippet. FastAPI remains authoritative and verifies every returned quote against a
workspace-scoped `RawArtifact` before persistence.

## Configuration

Required at runtime:

```text
BRIGHT_DATA_API_KEY
BRIGHT_DATA_SERP_ZONE
BRIGHT_DATA_UNLOCKER_ZONE
OPENROUTER_API_KEY
```

Optional: `PORT` (default `8090`), `HOST` (default `0.0.0.0`), and
`OPEN_ENRICH_CORE_MODULE` (default `@brightdata/enrich-core`). Secrets are never
returned or logged. Bind the published Docker port to an internal interface or use
an unexposed Docker network; this service has no end-user authentication.

## Local build

Open Enrich's core package is currently private to its pnpm workspace and is not
published to npm. For local execution, build the upstream core and install its pack
archive before starting this wrapper. TypeScript compilation itself does not require
the core package:

```bash
npm install
npm run build
npm start
```

The Docker build performs the upstream checkout/build/install automatically and pins
the audited commit by default:

```bash
docker build -t open-enrich-wrapper .
docker run --rm --env-file .env --network internal open-enrich-wrapper
```

Jobs are held in process memory. A restart loses active jobs, so run one replica for
this PoC. Production multi-replica deployment requires Redis/PostgreSQL-backed job
state and distributed cancellation.
