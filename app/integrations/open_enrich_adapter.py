import uuid
from datetime import UTC, datetime

from app.schemas.intelligence_provider import (
    EnrichmentFact,
    EnrichmentJobAccepted,
    EnrichmentJobRequest,
    EnrichmentJobResult,
    EnrichmentJobStatus,
    ProviderCitation,
)


class OpenEnrichAdapter:
    """Deterministic in-memory contract adapter for the Node service PoC."""

    provider_name = "open_enrich"

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}

    async def submit_job(self, request: EnrichmentJobRequest) -> EnrichmentJobAccepted:
        provider_job_id = f"oe_{uuid.uuid4().hex}"
        self._jobs[provider_job_id] = {"request": request, "poll_count": 0}
        return EnrichmentJobAccepted(
            provider_job_id=provider_job_id,
            status="queued",
            accepted_at=datetime.now(UTC),
        )

    async def get_job_status(self, provider_job_id: str) -> EnrichmentJobStatus:
        job = self._job(provider_job_id)
        job["poll_count"] += 1
        processing = job["poll_count"] == 1
        return EnrichmentJobStatus(
            provider_job_id=provider_job_id,
            status="processing" if processing else "completed",
            stage="enriching" if processing else "completed",
            tool_calls_used=1,
            cost_usd=0,
        )

    async def fetch_results(self, provider_job_id: str) -> EnrichmentJobResult:
        job = self._job(provider_job_id)
        request: EnrichmentJobRequest = job["request"]
        if job["poll_count"] < 2:
            raise RuntimeError("enrichment job is not completed")
        source_url = request.website_url or "https://example.com"
        field = request.allowed_fields[0]
        quote = f"{request.company_name} 的公开信息显示存在新的业务信号。"
        return EnrichmentJobResult(
            provider_job_id=provider_job_id,
            status="completed",
            facts=[
                EnrichmentFact(
                    field=field,
                    value=["新的业务信号"],
                    category="buying_signal",
                    provider_confidence=0.8,
                    citations=[ProviderCitation(url=source_url, quote=quote)],
                )
            ],
            tool_calls_used=1,
            cost_usd=0,
            provider_metadata={"mode": "mock"},
        )

    async def cancel_job(self, provider_job_id: str) -> None:
        job = self._job(provider_job_id)
        job["cancelled"] = True

    def _job(self, provider_job_id: str) -> dict:
        try:
            return self._jobs[provider_job_id]
        except KeyError as exc:
            raise KeyError(f"unknown Open Enrich job: {provider_job_id}") from exc
