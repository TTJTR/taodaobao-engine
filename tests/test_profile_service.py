import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import CustomerProfile, ProfileStatus
from app.services import profile_service as module


class FakeProfileRepository:
    existing: CustomerProfile | None = None
    create_count = 0

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id

    async def get_by_customer_name(self, customer_name: str) -> CustomerProfile | None:
        if self.existing and self.existing.customer_name == customer_name:
            return self.existing
        return None

    async def create(self, **values) -> CustomerProfile:
        type(self).create_count += 1
        profile = CustomerProfile(id=uuid.uuid4(), workspace_id=self.workspace_id, **values)
        profile.sources = []
        type(self).existing = profile
        return profile

    async def get(self, profile_id: uuid.UUID) -> CustomerProfile | None:
        return self.existing if self.existing and self.existing.id == profile_id else None


class FakeJobRepository:
    active = False

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id

    async def has_active_for_target(self, profile_id, job_type) -> bool:
        return self.active


@pytest.fixture(autouse=True)
def fake_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeProfileRepository.existing = None
    FakeProfileRepository.create_count = 0
    FakeJobRepository.active = False
    monkeypatch.setattr(module, "CustomerProfileRepository", FakeProfileRepository)
    monkeypatch.setattr(module, "SourceRepository", lambda *_: AsyncMock())
    monkeypatch.setattr(module, "JobRepository", FakeJobRepository)


@pytest.mark.asyncio
async def test_same_workspace_and_customer_name_returns_existing_profile() -> None:
    session = AsyncMock()
    service = module.CustomerProfileService(session, uuid.uuid4(), uuid.uuid4())

    first, first_created = await service.create("神州零售客户")
    second, second_created = await service.create("  神州零售客户  ")

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert FakeProfileRepository.create_count == 1
    assert session.commit.await_count == 1
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_confirm_marks_profile_confirmed() -> None:
    session = AsyncMock()
    user_id = uuid.uuid4()
    service = module.CustomerProfileService(session, uuid.uuid4(), user_id)
    profile, _ = await service.create("制造客户")
    profile.profile = {"background": "已完成 AI 提取的客户背景"}

    await service.confirm(profile.id)

    assert profile.status == ProfileStatus.CONFIRMED
    assert profile.confirmed_by_id == user_id
    assert profile.confirmed_at is not None
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_confirm_rejects_profile_while_generation_is_active() -> None:
    session = AsyncMock()
    service = module.CustomerProfileService(session, uuid.uuid4(), uuid.uuid4())
    profile, _ = await service.create("制造业客户")
    FakeJobRepository.active = True

    with pytest.raises(module.AppError) as raised:
        await service.confirm(profile.id)

    assert raised.value.status_code == 409
    assert profile.status == ProfileStatus.PENDING_CONFIRMATION
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_confirm_rejects_unprocessed_supplemental_text() -> None:
    session = AsyncMock()
    service = module.CustomerProfileService(session, uuid.uuid4(), uuid.uuid4())
    profile, _ = await service.create("汽车制造客户")
    profile.profile = {"supplemental_text": "未经 AI 提取的原始客户背景"}

    with pytest.raises(module.AppError) as raised:
        await service.confirm(profile.id)

    assert raised.value.status_code == 409
    assert profile.status == ProfileStatus.PENDING_CONFIRMATION
    assert session.commit.await_count == 1
