import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from app.ai.embedding import MockEmbeddingProvider
from app.db.models import Experience, ReviewStatus, Source, SourceFreshness
from app.services import asset_service as module
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


class FakeSourceRepository:
    source: Source

    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        pass

    async def get(self, source_id: uuid.UUID) -> Source | None:
        return self.source if self.source.id == source_id else None


class FakeContributionRepository:
    def __init__(self, session, workspace_id: uuid.UUID) -> None:
        pass

    async def get_for_user_source_role(self, *args):
        return None

    async def create(self, **values):
        return values


@pytest.fixture
def experience(monkeypatch: pytest.MonkeyPatch) -> Experience:
    asset = Experience(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        data={
            "name": "试点经验",
            "applicable_problem": "门店质检试点",
            "solution": "先旁路验证",
        },
        review_status=ReviewStatus.PENDING_REVIEW,
    )
    FakeExperienceRepository.asset = asset
    FakeExperienceRepository.last_review_filter = None
    FakeSourceRepository.source = Source(
        id=asset.source_id,
        workspace_id=asset.workspace_id,
        imported_by_id=uuid.uuid4(),
        title="来源",
        type="pasted_text",
        purpose="experience",
        status="pending_review",
        freshness_status=SourceFreshness.CURRENT,
        content_version=1,
        tags=[],
    )
    monkeypatch.setattr(ExperienceService, "repository_type", FakeExperienceRepository)
    monkeypatch.setattr(module, "SourceRepository", FakeSourceRepository)
    monkeypatch.setattr(module, "ExpertContributionRepository", FakeContributionRepository)
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
    session.add = Mock()
    service = ExperienceService(
        session,
        experience.workspace_id,
        uuid.uuid4(),
        MockEmbeddingProvider(dimension=1024),
    )

    result = await service.review(experience.id, action, "审核备注")

    assert result.review_status == expected
    assert result.review_note == "审核备注"
    assert result.embedding_ready is (action == "approve")
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
