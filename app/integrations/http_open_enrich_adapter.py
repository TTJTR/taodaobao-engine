import httpx
from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode
from app.schemas.intelligence_provider import (
    EnrichmentJobAccepted,
    EnrichmentJobRequest,
    EnrichmentJobResult,
    EnrichmentJobStatus,
)


class HttpOpenEnrichAdapter:
    provider_name = "open_enrich"

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._headers = headers
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=5.0, read=30.0, write=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def submit_job(self, request: EnrichmentJobRequest) -> EnrichmentJobAccepted:
        data = await self._request(
            "POST", "/internal/v1/enrichment-jobs", json=request.model_dump(mode="json")
        )
        return self._parse(EnrichmentJobAccepted, data)

    async def get_job_status(self, provider_job_id: str) -> EnrichmentJobStatus:
        data = await self._request("GET", f"/internal/v1/enrichment-jobs/{provider_job_id}")
        return self._parse(EnrichmentJobStatus, data)

    async def fetch_results(self, provider_job_id: str) -> EnrichmentJobResult:
        data = await self._request(
            "GET", f"/internal/v1/enrichment-jobs/{provider_job_id}/results"
        )
        return self._parse(EnrichmentJobResult, data)

    async def cancel_job(self, provider_job_id: str) -> None:
        await self._request("DELETE", f"/internal/v1/enrichment-jobs/{provider_job_id}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            request_headers = {**self._headers, **kwargs.pop("headers", {})}
            response = await self.client.request(
                method,
                f"{self.base_url}{path}",
                headers=request_headers,
                **kwargs,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("provider returned non-object JSON")
            return data
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Open Enrich internal service rejected the request",
                status_code=503 if retryable else 502,
                retryable=retryable,
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Open Enrich internal service is temporarily unavailable",
                status_code=503,
                retryable=True,
            ) from exc

    @staticmethod
    def _parse(model, data):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Open Enrich returned invalid protocol data",
                status_code=502,
                retryable=False,
            ) from exc
