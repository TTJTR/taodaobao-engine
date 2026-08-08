import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import Job, ProcessStatus, Source, SourcePurpose, SourceStatus, SourceType
from app.integrations.protocols import FeishuDocument
from app.services import job_runner as module


class FakeRepository:
    def __init__(self, entity) -> None:
        self.entity = entity

    async def get(self, entity_id: uuid.UUID):
        return self.entity if self.entity.id == entity_id else None


class Adapter:
    async def fetch_document(self, url: str) -> FeishuDocument:
        return FeishuDocument("同步标题", "正文", "作者", url, "2026-08-07T00:00:00Z")


class FailingAdapter(Adapter):
    async def fetch_document(self, url: str) -> FeishuDocument:
        raise RuntimeError("temporary upstream error")


def make_entities() -> tuple[uuid.UUID, Source, Job]:
    workspace_id = uuid.uuid4()
    source = Source(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        imported_by_id=uuid.uuid4(),
        type=SourceType.FEISHU_DOC,
        purpose=SourcePurpose.EXPERIENCE,
        title="待同步",
        source_url="https://example.feishu.cn/docx/demo",
        status=SourceStatus.PENDING,
        tags=[],
    )
    job = Job(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        type="source_processing",
        target_id=source.id,
        status=ProcessStatus.PENDING,
        stage="pending",
        retry_count=0,
    )
    return workspace_id, source, job


def configure_runner(monkeypatch, source: Source, job: Job) -> AsyncMock:
    session = AsyncMock()
    session.add = Mock()
    stages: list[str] = []

    async def capture_commit() -> None:
        stages.append(job.stage)

    session.commit.side_effect = capture_commit

    @asynccontextmanager
    async def session_context():
        yield session

    monkeypatch.setattr(module, "get_session_factory", lambda: session_context)
    monkeypatch.setattr(module, "JobRepository", lambda *_: FakeRepository(job))
    monkeypatch.setattr(module, "SourceRepository", lambda *_: FakeRepository(source))
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    session.observed_stages = stages
    return session


@pytest.mark.asyncio
async def test_job_runner_flows_to_pending_review(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id, source, job = make_entities()
    session = configure_runner(monkeypatch, source, job)

    await module.run_source_job(job.id, source.id, workspace_id, Adapter())

    assert session.observed_stages == [
        "fetching",
        "fetching",
        "parsing",
        "extracting",
        "pending_review",
    ]
    assert job.status == ProcessStatus.COMPLETED
    assert source.status == SourceStatus.PENDING_REVIEW
    assert source.title == "同步标题"


@pytest.mark.asyncio
async def test_job_runner_records_failure_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id, source, job = make_entities()
    configure_runner(monkeypatch, source, job)

    await module.run_source_job(job.id, source.id, workspace_id, FailingAdapter())

    assert job.status == ProcessStatus.FAILED
    assert source.status == SourceStatus.FAILED
    assert job.stage == "failed"
    assert job.error_code == "SOURCE_TEMPORARILY_UNAVAILABLE"
    assert job.retry_count == 1
    assert "upstream" in (job.error_summary or "")
