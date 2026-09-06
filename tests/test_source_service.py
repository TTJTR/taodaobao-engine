import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import Job, ProcessStatus, Source, SourcePurpose, SourceType
from app.services import source_service as module


class FakeSourceRepository:
    entity: Source | None = None
    create_count = 0

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id

    async def get_by_source_url(self, source_url: str) -> Source | None:
        if self.entity is not None and self.entity.source_url == source_url:
            return self.entity
        return None

    async def create(self, **values) -> Source:
        type(self).create_count += 1
        source = Source(id=uuid.uuid4(), workspace_id=self.workspace_id, **values)
        type(self).entity = source
        return source

    async def update(self, source: Source, **values) -> Source:
        for key, value in values.items():
            setattr(source, key, value)
        return source


class FakeJobRepository:
    created: list[Job] = []

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id

    async def create(self, **values) -> Job:
        job = Job(id=uuid.uuid4(), workspace_id=self.workspace_id, **values)
        type(self).created.append(job)
        return job


@pytest.fixture(autouse=True)
def reset_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSourceRepository.entity = None
    FakeSourceRepository.create_count = 0
    FakeJobRepository.created = []
    monkeypatch.setattr(module, "SourceRepository", FakeSourceRepository)
    monkeypatch.setattr(module, "JobRepository", FakeJobRepository)


@pytest.mark.asyncio
async def test_import_link_updates_one_source_and_creates_a_job_each_time() -> None:
    session = AsyncMock()
    workspace_id = uuid.uuid4()
    service = module.SourceService(session, workspace_id, uuid.uuid4())
    url = "https://example.feishu.cn/docx/demo"

    first = await service.import_link(
        url=url,
        purpose=SourcePurpose.CUSTOMER_PROFILE,
        customer_profile_id=None,
    )
    second = await service.import_link(
        url=url,
        purpose=SourcePurpose.EXPERIENCE,
        customer_profile_id=None,
    )

    assert first.source.id == second.source.id
    assert second.source.purpose == SourcePurpose.EXPERIENCE
    assert FakeSourceRepository.create_count == 1
    assert len(FakeJobRepository.created) == 2
    assert all(job.status == ProcessStatus.PENDING for job in FakeJobRepository.created)
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_import_text_creates_source_and_pending_job() -> None:
    session = AsyncMock()
    service = module.SourceService(session, uuid.uuid4(), uuid.uuid4())

    result = await service.import_text(
        title="会议纪要",
        content="客户需要两周内完成试点。",
        purpose=SourcePurpose.CUSTOMER_PROFILE,
        customer_profile_id=None,
        is_demo=True,
    )

    assert result.source.type == SourceType.PASTED_TEXT
    assert result.source.content == "客户需要两周内完成试点。"
    assert result.source.is_demo is True
    assert result.job.target_id == result.source.id
    assert result.job.stage == "pending"
