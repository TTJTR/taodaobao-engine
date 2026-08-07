import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import Experience, ReviewStatus
from app.services.asset_service import ExperienceService


class FakeExperienceRepository:
    asset: Experience
    last_review_filter: str | None = None

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        pass

    async def get(self, asset_id: uuid.UUID) -> Experience | None:
        return self.asset if self.asset.id == asset_id else None

    async def list_filtered(self, **values) -> list[Experience]:
        type(self).last_review_filter = values["review_status"]
        return [self.asset]

    async def count_filtered(self, **values) -> int:
        return 1


@pytest.fixture
def experience(monkeypatch: pytest.MonkeyPatch) -> Experience:
    asset = Experience(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        data={"name": "试点经验"},
        review_status=ReviewStatus.PENDING_REVIEW,
    )
    FakeExperienceRepository.asset = asset
    FakeExperienceRepository.last_review_filter = None
    monkeypatch.setattr(ExperienceService, "repository_type", FakeExperienceRepository)
    return asset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("approve", ReviewStatus.VERIFIED),
        ("reject", ReviewStatus.REJECTED),
        ("reopen", ReviewStatus.PENDING_REVIEW),
    ],
)
async def test_review_maps_action_and_saves_note(
    experience: Experience, action: str, expected: ReviewStatus
) -> None:
    session = AsyncMock()
    service = ExperienceService(session, experience.workspace_id)

    result = await service.review(experience.id, action, "审核备注")

    assert result.review_status == expected
    assert result.review_note == "审核备注"
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(experience)


@pytest.mark.asyncio
async def test_list_passes_review_status_filter(experience: Experience) -> None:
    service = ExperienceService(AsyncMock(), experience.workspace_id)

    items, total = await service.list_assets(
        page=1,
        page_size=20,
        keyword=None,
        review_status=ReviewStatus.VERIFIED,
    )

    assert items == [experience]
    assert total == 1
    assert FakeExperienceRepository.last_review_filter == "verified"
