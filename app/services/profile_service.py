import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import CustomerProfile, Job, JobType, ProcessStatus, ProfileStatus
from app.db.repositories import CustomerProfileRepository, JobRepository, SourceRepository


class CustomerProfileService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.profiles = CustomerProfileRepository(session, workspace_id)
        self.sources = SourceRepository(session, workspace_id)
        self.jobs = JobRepository(session, workspace_id)
        self.user_id = user_id

    async def create(self, customer_name: str) -> tuple[CustomerProfile, bool]:
        normalized_name = customer_name.strip()
        await self._lock_name(normalized_name)
        existing = await self.profiles.get_by_customer_name(normalized_name)
        if existing is not None:
            return existing, False
        profile = await self.profiles.create(
            customer_name=normalized_name,
            profile={},
            status=ProfileStatus.PENDING_CONFIRMATION,
        )
        await self.session.commit()
        await self.session.refresh(profile)
        return await self.get(profile.id), True

    async def get(self, profile_id: uuid.UUID) -> CustomerProfile:
        profile = await self.profiles.get(profile_id)
        if profile is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "客户画像不存在", status_code=404)
        return profile

    async def list_profiles(
        self, *, page: int, page_size: int, keyword: str | None
    ) -> tuple[list[CustomerProfile], int]:
        items = await self.profiles.list_filtered(
            offset=(page - 1) * page_size, limit=page_size, keyword=keyword
        )
        return items, await self.profiles.count_filtered(keyword=keyword)

    async def update(
        self,
        profile_id: uuid.UUID,
        *,
        customer_name: str | None,
        profile_data: dict,
        source_ids: list[uuid.UUID] | None,
    ) -> CustomerProfile:
        profile = await self.get(profile_id)
        if customer_name is not None and customer_name.strip() != profile.customer_name:
            normalized_name = customer_name.strip()
            await self._lock_name(normalized_name)
            duplicate = await self.profiles.get_by_customer_name(normalized_name)
            if duplicate is not None and duplicate.id != profile.id:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    "同名客户画像已存在",
                    status_code=409,
                )
            profile.customer_name = normalized_name
        profile.profile = profile_data
        profile.status = ProfileStatus.PENDING_CONFIRMATION
        profile.confirmed_by_id = None
        profile.confirmed_at = None
        if source_ids is not None:
            await self._replace_sources(profile, source_ids)
        await self.session.commit()
        await self.session.refresh(profile)
        return await self.get(profile.id)

    async def generate(
        self,
        profile_id: uuid.UUID,
        *,
        source_ids: list[uuid.UUID],
        supplemental_text: str | None,
    ) -> Job:
        profile = await self.get(profile_id)
        if source_ids:
            await self._replace_sources(profile, source_ids)
        # The AI worker reads source links and supplemental text in the later Harness phase.
        if supplemental_text:
            profile.profile = {**profile.profile, "supplemental_text": supplemental_text}
        profile.status = ProfileStatus.PENDING_CONFIRMATION
        job = await self.jobs.create(
            type=JobType.PROFILE_GENERATION,
            target_id=profile.id,
            status=ProcessStatus.PENDING,
            stage="pending",
            retry_count=0,
        )
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def confirm(self, profile_id: uuid.UUID) -> None:
        profile = await self.get(profile_id)
        if self.user_id is None:
            raise AppError(ErrorCode.AUTH_REQUIRED, "缺少画像确认人", status_code=401)
        profile.status = ProfileStatus.CONFIRMED
        profile.confirmed_by_id = self.user_id
        profile.confirmed_at = datetime.now(UTC)
        await self.session.commit()

    async def _replace_sources(self, profile: CustomerProfile, source_ids: list[uuid.UUID]) -> None:
        selected = []
        for source_id in dict.fromkeys(source_ids):
            source = await self.sources.get(source_id)
            if source is None:
                raise AppError(ErrorCode.SOURCE_NOT_FOUND, "关联资料不存在", status_code=404)
            selected.append(source)
        for source in profile.sources:
            if source.id not in source_ids:
                source.customer_profile_id = None
        for source in selected:
            source.customer_profile_id = profile.id

    async def _lock_name(self, customer_name: str) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.workspace_id}:customer-profile:{customer_name}"},
        )
