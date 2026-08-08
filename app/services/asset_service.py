import uuid
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embedding import AssetType, EmbeddingProvider
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    Capability,
    ContributionRole,
    Experience,
    ReviewRecord,
    ReviewStatus,
    SourceFreshness,
    SourceStatus,
)
from app.db.repositories import (
    CapabilityRepository,
    ExperienceRepository,
    ExpertContributionRepository,
    SourceRepository,
)

AssetT = TypeVar("AssetT", Experience, Capability)


class AssetService(Generic[AssetT]):
    repository_type: type
    label: str
    asset_type: AssetType
    required_fields: tuple[str, ...]

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.embedding_provider = embedding_provider
        self.repository = self.repository_type(session, workspace_id)
        self.sources = SourceRepository(session, workspace_id)
        self.contributions = ExpertContributionRepository(session, workspace_id)

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
        total = await self.repository.count_filtered(keyword=keyword, review_status=status_value)
        return items, total

    async def update(self, asset_id: uuid.UUID, data: dict) -> AssetT:
        asset = await self.get(asset_id)
        asset.data = data
        if asset.review_status == ReviewStatus.VERIFIED:
            asset.review_status = ReviewStatus.PENDING_REVIEW
            asset.embedding_ready = False
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def review(self, asset_id: uuid.UUID, action: str, note: str | None) -> AssetT:
        asset = await self.get(asset_id)
        target_status = {
            "approve": ReviewStatus.VERIFIED,
            "reject": ReviewStatus.REJECTED,
            "reopen": ReviewStatus.PENDING_REVIEW,
        }[action]
        if asset.review_status == target_status and asset.review_note == note:
            return asset
        source = await self.sources.get(asset.source_id)
        if source is None:
            raise AppError(ErrorCode.SOURCE_NOT_FOUND, "原始资料不存在", status_code=409)
        before = {"review_status": asset.review_status.value, "data": asset.data}
        if action == "approve":
            await self._prepare_verified_asset(asset, source)
            source.status = SourceStatus.COMPLETED
        else:
            if action == "reject" and not (note or "").strip():
                raise AppError(ErrorCode.VALIDATION_FAILED, "打回时必须填写原因", status_code=422)
            asset.embedding_ready = False
        asset.review_status = target_status
        asset.review_note = note
        await self._record_review(asset, source, action, note, before, target_status)
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def _prepare_verified_asset(self, asset: AssetT, source) -> None:
        if self.user_id is None or self.embedding_provider is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "审核服务未配置审核人或Embedding适配器",
                status_code=500,
            )
        missing = [field for field in self.required_fields if not asset.data.get(field)]
        if missing:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                f"缺少必填字段：{', '.join(missing)}",
                status_code=422,
            )
        if source.freshness_status != SourceFreshness.CURRENT:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "原文已更新、无权限或已删除，不能通过审核",
                status_code=409,
            )
        embedding_text = str(
            asset.data.get("embedding_text")
            or "。".join(str(asset.data[field]) for field in self.required_fields)
        )
        embedded = await self.embedding_provider.embed(embedding_text, self.asset_type)
        if embedded.dimension != 1024:
            raise AppError(ErrorCode.INTERNAL_ERROR, "Embedding维度必须为1024", status_code=500)
        asset.embedding = embedded.vector
        asset.embedding_text = embedding_text
        asset.embedding_version = embedded.embedding_version
        asset.embedding_fingerprint = embedded.content_fingerprint
        asset.embedding_ready = True
        asset.reviewed_by_id = self.user_id
        asset.reviewed_at = datetime.now(UTC)
        asset.source_version_at_review = source.content_version

    async def _record_review(self, asset, source, action, note, before, target_status) -> None:
        if self.user_id is None:
            return
        self.session.add(
            ReviewRecord(
                workspace_id=self.workspace_id,
                asset_type=self.asset_type.value,
                asset_id=asset.id,
                action=action,
                reviewer_id=self.user_id,
                note=note,
                before_data=before,
                after_data={"review_status": target_status.value, "data": asset.data},
            )
        )
        if action == "approve":
            contribution = await self.contributions.get_for_user_source_role(
                self.user_id, source.id, ContributionRole.REVIEWER
            )
            values = {
                "asset_type": self.asset_type.value,
                "asset_id": asset.id,
                "title": str(asset.data.get("name") or self.label),
                "tags": list(asset.data.get("tags") or []),
            }
            if contribution is None:
                await self.contributions.create(
                    user_id=self.user_id,
                    source_id=source.id,
                    role=ContributionRole.REVIEWER,
                    **values,
                )
            else:
                await self.contributions.update(contribution, **values)


class ExperienceService(AssetService[Experience]):
    repository_type = ExperienceRepository
    label = "经验资产"
    asset_type = AssetType.EXPERIENCE
    required_fields = ("name", "applicable_problem", "solution")


class CapabilityService(AssetService[Capability]):
    repository_type = CapabilityRepository
    label = "能力资产"
    asset_type = AssetType.CAPABILITY
    required_fields = ("name", "description", "inputs", "outputs", "limitations")
