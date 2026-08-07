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


@pytest.fixture(autouse=True)
def fake_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeProfileRepository.existing = None
    FakeProfileRepository.create_count = 0
    monkeypatch.setattr(module, "CustomerProfileRepository", FakeProfileRepository)
    monkeypatch.setattr(module, "SourceRepository", lambda *_: AsyncMock())
    monkeypatch.setattr(module, "JobRepository", lambda *_: AsyncMock())


@pytest.mark.asyncio
async def test_same_workspace_and_customer_name_returns_existing_profile() -> None:
    session = AsyncMock()
    service = module.CustomerProfileService(session, uuid.uuid4())

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
    service = module.CustomerProfileService(session, uuid.uuid4())
    profile, _ = await service.create("制造客户")

    await service.confirm(profile.id)

    assert profile.status == ProfileStatus.CONFIRMED
    assert session.commit.await_count == 2
