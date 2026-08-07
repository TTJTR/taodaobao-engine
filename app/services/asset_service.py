import uuid
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import Capability, Experience, ReviewStatus
from app.db.repositories import CapabilityRepository, ExperienceRepository

AssetT = TypeVar("AssetT", Experience, Capability)


class AssetService(Generic[AssetT]):
    repository_type: type
    label: str

    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.repository = self.repository_type(session, workspace_id)

    async def get(self, asset_id: uuid.UUID) -> AssetT:
        asset = await self.repository.get(asset_id)
        if asset is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, f"{self.label}不存在", status_code=404)
        return asset

    async def list_assets(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str | None,
        review_status: ReviewStatus | None,
    ) -> tuple[list[AssetT], int]:
        status_value = review_status.value if review_status else None
        items = await self.repository.list_filtered(
            offset=(page - 1) * page_size,
            limit=page_size,
            keyword=keyword,
            review_status=status_value,
        )
        total = await self.repository.count_filtered(
            keyword=keyword, review_status=status_value
        )
        return items, total

    async def update(self, asset_id: uuid.UUID, data: dict) -> AssetT:
        asset = await self.get(asset_id)
        asset.data = data
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def review(self, asset_id: uuid.UUID, action: str, note: str | None) -> AssetT:
        asset = await self.get(asset_id)
        asset.review_status = {
            "approve": ReviewStatus.VERIFIED,
            "reject": ReviewStatus.REJECTED,
            "reopen": ReviewStatus.PENDING_REVIEW,
        }[action]
        asset.review_note = note
        await self.session.commit()
        await self.session.refresh(asset)
        return asset


class ExperienceService(AssetService[Experience]):
    repository_type = ExperienceRepository
    label = "经验资产"


class CapabilityService(AssetService[Capability]):
    repository_type = CapabilityRepository
    label = "能力资产"
