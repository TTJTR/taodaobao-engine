import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    CustomerProfile,
    IntelligenceSnapshot,
    ProfileIntelligenceProposal,
    ProfileStatus,
    RawArtifact,
    RawArtifactKind,
    RawArtifactStatus,
    SearchRun,
    SearchRunStatus,
    User,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.integrations.http_open_enrich_adapter import HttpOpenEnrichAdapter
from app.schemas.intelligence_provider import (
    EnrichmentJobRequest,
    EnrichmentJobResult,
    EnrichmentJobStatus,
)
from app.services.intelligence_service import IntelligenceService
from app.services.intelligence_task_worker import (
    _capture_provider_sources,
    _fail,
    _final_run_status,
    _limit_error,
    _log_terminal_task,
    _poll_once,
    run_open_enrich_task,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def enrichment_request(**overrides) -> EnrichmentJobRequest:
    values = {
        "client_job_id": uuid.uuid4(),
        "company_name": "Example",
        "website_url": "https://example.com",
        "allowed_fields": ["buying_signals"],
        "max_cost_usd": 2,
        "max_tool_calls": 10,
    }
    values.update(overrides)
    return EnrichmentJobRequest(**values)


class QuotaProvider:
    def __init__(self) -> None:
        self.polls = 0
        self.cancelled = []
        self.fetched = False

    async def get_job_status(self, provider_job_id: str) -> EnrichmentJobStatus:
        self.polls += 1
        return EnrichmentJobStatus(
            provider_job_id=provider_job_id,
            status="processing",
            stage="enriching",
            cost_usd=1 if self.polls == 1 else 2.01,
            tool_calls_used=self.polls,
        )

    async def cancel_job(self, provider_job_id: str) -> None:
        self.cancelled.append(provider_job_id)

    async def fetch_results(self, provider_job_id: str):
        self.fetched = True
        raise AssertionError("quota-exceeded job must not fetch results")


@pytest.mark.asyncio
async def test_second_poll_exceeding_cost_cancels_and_fails_task() -> None:
    provider = QuotaProvider()
    task = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        payload={"provider_job_id": "oe_quota", "poll_count": 0},
        status=WorkflowTaskStatus.RUNNING,
        stage="polling",
        error_code=None,
        error_summary=None,
        finished_at=None,
        available_at=None,
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
    )
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status=SearchRunStatus.RUNNING,
        error_code=None,
        error_summary=None,
        completed_at=None,
    )
    request = enrichment_request()

    await _poll_once(None, task, run, request, uuid.uuid4(), uuid.uuid4(), provider)
    task.status = WorkflowTaskStatus.RUNNING
    await _poll_once(None, task, run, request, uuid.uuid4(), uuid.uuid4(), provider)

    assert provider.cancelled == ["oe_quota"]
    assert provider.fetched is False
    assert task.status == WorkflowTaskStatus.FAILED
    assert task.error_code == ErrorCode.QUOTA_EXCEEDED.value
    assert run.status == SearchRunStatus.FAILED


def test_limits_trip_at_the_exact_boundary_and_report_the_cause() -> None:
    request = enrichment_request(max_cost_usd=2, max_tool_calls=10)

    assert _limit_error(request, 1.99, 9) is None
    assert _limit_error(request, 1.99, 10) == "TOOL_CALL_LIMIT_EXCEEDED"
    assert _limit_error(request, 2, 9) == ErrorCode.QUOTA_EXCEEDED.value


def test_optional_provider_failure_preserves_primary_search_result() -> None:
    task = SimpleNamespace(
        status=WorkflowTaskStatus.RUNNING,
        stage="polling",
        error_code=None,
        error_summary=None,
        finished_at=None,
        lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
    )
    run = SimpleNamespace(
        status=SearchRunStatus.RUNNING,
        error_code=None,
        error_summary=None,
        completed_at=None,
        result_summary={
            "provider": "bailian_web_search",
            "provider_chain": ["bailian_web_search", "open_enrich"],
            "captured": 3,
        },
    )

    _fail(task, run, "PROVIDER_UNAVAILABLE", "optional provider unavailable")

    assert task.status == WorkflowTaskStatus.FAILED
    assert run.status == SearchRunStatus.PARTIAL
    assert run.error_code is None
    assert run.result_summary["captured"] == 3
    assert run.result_summary["open_enrich"] == {
        "status": "failed",
        "error_code": "PROVIDER_UNAVAILABLE",
        "error_summary": "optional provider unavailable",
    }


def test_primary_warning_is_not_erased_by_successful_optional_provider() -> None:
    previous_summary = {
        "provider": "bailian_web_search",
        "provider_chain": ["bailian_web_search", "open_enrich"],
        "skipped": 1,
        "enrichment_error_count": 0,
    }

    assert _final_run_status("completed", previous_summary) == SearchRunStatus.PARTIAL
    assert _final_run_status("completed", {**previous_summary, "skipped": 0}) == (
        SearchRunStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_provider_citation_is_refetched_before_it_can_be_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    citation_url = "https://public.example.com/facts"
    captured = SimpleNamespace(source_url=citation_url)

    class FakeRows:
        def all(self):
            return []

    class FakeSession:
        async def execute(self, _statement):
            return FakeRows()

    class FakeScraper:
        def __init__(self):
            self.urls = []

        async def fetch(self, url):
            self.urls.append(url)
            return captured

    class FakeService:
        def __init__(self):
            self.persisted = None

        async def persist_provider_captures(self, **kwargs):
            self.persisted = kwargs

    scraper = FakeScraper()
    service = FakeService()
    monkeypatch.setattr(
        "app.services.intelligence_task_worker.WebScraperAdapter", lambda: scraper
    )
    result = EnrichmentJobResult.model_validate(
        {
            "provider_job_id": "oe-public-source",
            "status": "completed",
            "facts": [
                {
                    "field": "buying_signals",
                    "value": "发布公开采购计划",
                    "category": "buying_signal",
                    "provider_confidence": 0.8,
                    "citations": [
                        {
                            "url": citation_url,
                            "quote": "发布公开采购计划",
                        }
                    ],
                }
            ],
        }
    )
    run = SimpleNamespace(id=uuid.uuid4(), workspace_id=uuid.uuid4())

    await _capture_provider_sources(
        FakeSession(),
        service=service,
        run=run,
        result=result,
        provider_job_id="oe-public-source",
    )

    assert scraper.urls == [citation_url]
    assert service.persisted == {
        "run_id": run.id,
        "captures": [captured],
        "provider_job_id": "oe-public-source",
    }


def test_terminal_log_has_fixed_observability_fields(caplog) -> None:
    now = datetime.now(UTC)
    task = SimpleNamespace(
        id=uuid.uuid4(),
        status=WorkflowTaskStatus.FAILED,
        error_code="QUOTA_EXCEEDED",
        created_at=now - timedelta(seconds=2),
        finished_at=now,
        payload={
            "provider_job_id": "provider-job-safe-id",
            "cost_usd": 0.1,
            "tool_calls_used": 3,
            "Authorization": "Bearer secret-must-not-appear",
        },
    )

    with caplog.at_level("INFO"):
        _log_terminal_task(task)

    event = json.loads(caplog.records[-1].message)
    assert event == {
        "event": "open_enrich_task_terminal",
        "task_id": str(task.id),
        "provider_job_id": "provider-job-safe-id",
        "status": "failed",
        "error_code": "QUOTA_EXCEEDED",
        "cost_usd": 0.1,
        "tool_calls": 3,
        "duration_ms": 2000,
    }
    assert "secret-must-not-appear" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "retryable"), [("500", True), ("400", False), ("timeout", True)]
)
async def test_http_adapter_translates_transport_failures(
    failure: str, retryable: bool
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            int(failure), request=request, json={"error": "secret upstream detail"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpOpenEnrichAdapter("http://open-enrich.internal", client=client)
    try:
        with pytest.raises(AppError) as caught:
            await adapter.submit_job(enrichment_request())
        assert caught.value.code == ErrorCode.PROVIDER_UNAVAILABLE
        assert caught.value.retryable is retryable
        assert "secret upstream detail" not in caught.value.message
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_adapter_sends_internal_bearer_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer internal-test-token"
        return httpx.Response(
            202,
            request=request,
            json={
                "provider_job_id": "provider-job",
                "status": "queued",
                "accepted_at": datetime.now(UTC).isoformat(),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = HttpOpenEnrichAdapter(
        "http://open-enrich.internal", token="internal-test-token", client=client
    )
    try:
        accepted = await adapter.submit_job(enrichment_request())
        assert accepted.provider_job_id == "provider-job"
    finally:
        await client.aclose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_combined_queue_is_optional_and_keeps_alibaba_as_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    workspace_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="combined-user", name="Reviewer")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="Example Group",
            profile={"external_intelligence": {}},
            status=ProfileStatus.CONFIRMED,
            confirmed_by_id=user.id,
            confirmed_at=datetime.now(UTC),
        )
        session.add(profile)
        await session.flush()
        service = IntelligenceService(session, workspace_id, user.id)

        monkeypatch.setattr(
            "app.services.intelligence_service.settings.open_enrich_svc_url", ""
        )
        monkeypatch.setattr(
            "app.services.intelligence_service.settings.open_enrich_svc_token", ""
        )
        options = {
            "company_name": "Example Group",
            "website_url": None,
            "allowed_fields": ["buying_signals"],
            "max_tool_calls": 10,
            "max_cost_usd": 1.0,
        }
        with pytest.raises(AppError) as caught:
            await service.queue_automatic_search(
                query="Example public signals",
                purpose="customer_profile",
                profile_id=profile.id,
                max_results=3,
                language="zh-CN",
                country="CN",
                open_enrich_options=options,
            )
        assert caught.value.code == ErrorCode.PROVIDER_UNAVAILABLE

        default_run, default_task = await service.queue_automatic_search(
            query="Example public signals",
            purpose="customer_profile",
            profile_id=profile.id,
            max_results=3,
            language="zh-CN",
            country="CN",
        )
        assert default_run.provider == "bailian_web_search"
        assert default_task.payload["open_enrich_request"] is None

        monkeypatch.setattr(
            "app.services.intelligence_service.settings.open_enrich_svc_url",
            "http://open-enrich-sidecar:8090",
        )
        monkeypatch.setattr(
            "app.services.intelligence_service.settings.open_enrich_svc_token",
            "internal-test-token",
        )
        combined_run, combined_task = await service.queue_automatic_search(
            query="Example public signals",
            purpose="customer_profile",
            profile_id=profile.id,
            max_results=3,
            language="zh-CN",
            country="CN",
            open_enrich_options=options,
        )
        assert combined_run.provider == "bailian_web_search+open_enrich"
        assert combined_run.input_snapshot["provider_chain"] == [
            "bailian_web_search",
            "open_enrich",
        ]
        assert combined_task.payload["open_enrich_request"]["max_cost_usd"] == 1.0
        assert combined_task.payload["open_enrich_request"]["max_tool_calls"] == 10
    await engine.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_persistent_contract_adapter_polling_creates_snapshot_and_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations.open_enrich_adapter import OpenEnrichAdapter

    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(
        "app.services.intelligence_task_worker.get_session_factory", lambda: factory
    )
    workspace_id = uuid.uuid4()
    provider = OpenEnrichAdapter()
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    # Patch only after SQLAlchemy has resolved and opened the test database.
    # The replacement validates public-source handling and must not intercept
    # the database driver's own DNS resolution.
    monkeypatch.setattr(
        "app.services.intelligence_service.socket.getaddrinfo",
        lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="oe-worker", name="Reviewer")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="Example",
            profile={"external_intelligence": {}},
            status=ProfileStatus.CONFIRMED,
            confirmed_by_id=user.id,
            confirmed_at=datetime.now(UTC),
        )
        session.add(profile)
        run = SearchRun(
            workspace_id=workspace_id,
            created_by_id=user.id,
            query="Example signals",
            purpose="customer_profile",
            provider="open_enrich",
            status=SearchRunStatus.QUEUED,
            trace_id=str(uuid.uuid4()),
            input_snapshot={},
            result_summary={},
        )
        session.add(run)
        await session.flush()
        request = enrichment_request(client_job_id=run.id)
        quote = "Example 的公开信息显示存在新的业务信号。"
        session.add(
            RawArtifact(
                workspace_id=workspace_id,
                search_run_id=run.id,
                artifact_key=uuid.uuid4().hex,
                kind=RawArtifactKind.WEB_PAGE,
                status=RawArtifactStatus.CAPTURED,
                provider="web_scraper",
                source_url="https://example.com",
                normalized_url="https://example.com/",
                mime_type="text/html",
                http_status=200,
                content_sha256="a" * 64,
                byte_size=len(quote.encode()),
                text_content=quote,
                captured_at=datetime.now(UTC),
                security_report={},
                metadata_snapshot={},
            )
        )
        task = WorkflowTask(
            workspace_id=workspace_id,
            kind="open_enrich",
            target_id=run.id,
            status=WorkflowTaskStatus.RUNNING,
            stage="queued",
            trace_id=run.trace_id,
            payload={
                "profile_id": str(profile.id),
                "user_id": str(user.id),
                "request": request.model_dump(mode="json"),
                "provider_job_id": None,
                "poll_count": 0,
            },
            max_attempts=200,
            available_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        session.add(task)
        await session.commit()
        ids = task.id, run.id, profile.id

    for _ in range(3):
        await run_open_enrich_task(ids[0], workspace_id, ids[1], provider)

    async with factory() as session:
        task = await session.get(WorkflowTask, ids[0])
        run = await session.get(SearchRun, ids[1])
        snapshot = await session.scalar(
            select(IntelligenceSnapshot).where(
                IntelligenceSnapshot.workspace_id == workspace_id,
                IntelligenceSnapshot.is_deleted.is_(False),
            )
        )
        proposal = await session.scalar(
            select(ProfileIntelligenceProposal).where(
                ProfileIntelligenceProposal.profile_id == ids[2],
                ProfileIntelligenceProposal.workspace_id == workspace_id,
                ProfileIntelligenceProposal.is_deleted.is_(False),
            )
        )
        assert task.status == WorkflowTaskStatus.COMPLETED
        assert run.status == SearchRunStatus.COMPLETED
        assert snapshot is not None
        assert proposal is not None
    await engine.dispose()
