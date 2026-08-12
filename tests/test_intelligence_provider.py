import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode
from app.integrations.open_enrich_adapter import OpenEnrichAdapter
from app.integrations.protocols import IntelligenceProvider
from app.schemas.intelligence_provider import (
    EnrichmentFact,
    EnrichmentJobRequest,
    EnrichmentJobResult,
    ProviderCitation,
)
from app.services.intelligence_service import _verify_provider_facts


def request(**overrides) -> EnrichmentJobRequest:
    values = {
        "client_job_id": uuid.uuid4(),
        "company_name": "示例企业",
        "website_url": "https://example.com",
        "allowed_fields": ["buying_signals"],
    }
    values.update(overrides)
    return EnrichmentJobRequest(**values)


def result(url: str, quote: str, field: str = "buying_signals") -> EnrichmentJobResult:
    return EnrichmentJobResult(
        provider_job_id="oe_test",
        status="completed",
        facts=[
            EnrichmentFact(
                field=field,
                value=["AI 质检项目"],
                category="buying_signal",
                provider_confidence=0.88,
                citations=[ProviderCitation(url=url, quote=quote)],
            )
        ],
    )


def artifact(url: str, content: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_url=url,
        normalized_url=url,
        text_content=content,
        captured_at=datetime.now(UTC),
    )


def test_provider_contract_rejects_duplicate_fields_and_missing_citations() -> None:
    with pytest.raises(ValidationError):
        request(allowed_fields=["buying_signals", "buying_signals"])
    with pytest.raises(ValidationError):
        EnrichmentFact(
            field="buying_signals",
            value="signal",
            category="buying_signal",
            provider_confidence=0.5,
            citations=[],
        )


@pytest.mark.asyncio
async def test_open_enrich_mock_follows_async_job_protocol() -> None:
    adapter = OpenEnrichAdapter()
    assert isinstance(adapter, IntelligenceProvider)
    accepted = await adapter.submit_job(request())

    first = await adapter.get_job_status(accepted.provider_job_id)
    second = await adapter.get_job_status(accepted.provider_job_id)
    completed = await adapter.fetch_results(accepted.provider_job_id)

    assert first.status == "processing"
    assert second.status == "completed"
    assert completed.facts[0].citations[0].quote


def test_provider_quote_is_verified_and_hashed_by_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote = "客户正在推进 AI 质检项目。"
    raw = artifact("https://example.com/news", f"公告内容：{quote}")
    monkeypatch.setattr(
        "app.services.intelligence_service.socket.getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )

    facts, links, proposed = _verify_provider_facts(
        request(), result(raw.source_url, quote), [raw]
    )

    citation = facts[0]["citations"][0]
    assert citation["quote_hash"] == hashlib.sha256(quote.encode()).hexdigest()
    assert links == {raw.id: raw}
    assert proposed == {"buying_signals": ["AI 质检项目"]}


def test_provider_fabricated_quote_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = artifact("https://example.com/news", "正文没有这个结论。")
    monkeypatch.setattr(
        "app.services.intelligence_service.socket.getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )

    with pytest.raises(AppError) as caught:
        _verify_provider_facts(request(), result(raw.source_url, "伪造的原文引文"), [raw])

    assert caught.value.code == ErrorCode.AI_OUTPUT_INVALID


def test_provider_private_source_and_unapproved_field_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = artifact("http://127.0.0.1/admin", "内部页面引文")
    with pytest.raises(AppError) as private_error:
        _verify_provider_facts(request(), result(private.source_url, "内部页面引文"), [private])
    assert private_error.value.code == ErrorCode.UNSAFE_EXTERNAL_URL

    public = artifact("https://example.com/news", "公开引文")
    monkeypatch.setattr(
        "app.services.intelligence_service.socket.getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    with pytest.raises(AppError) as field_error:
        _verify_provider_facts(
            request(), result(public.source_url, "公开引文", field="person_phone"), [public]
        )
    assert field_error.value.code == ErrorCode.AI_OUTPUT_INVALID


def test_provider_source_dns_failure_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = artifact("https://unresolvable.example/news", "公开引文")

    def unavailable(*_):
        import socket

        raise socket.gaierror

    monkeypatch.setattr("app.services.intelligence_service.socket.getaddrinfo", unavailable)
    with pytest.raises(AppError) as caught:
        _verify_provider_facts(request(), result(raw.source_url, "公开引文"), [raw])

    assert caught.value.code == ErrorCode.UNSAFE_EXTERNAL_URL
