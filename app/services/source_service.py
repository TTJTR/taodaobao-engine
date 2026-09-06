import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    Capability,
    Experience,
    Job,
    JobType,
    ProcessStatus,
    ReviewStatus,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
)
from app.db.repositories import JobRepository, SourceRepository


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    source: Source
    job: Job


class SourceService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.sources = SourceRepository(session, workspace_id)
        self.jobs = JobRepository(session, workspace_id)
        self.user_id = user_id

    async def import_link(
        self,
        *,
        url: str,
        purpose: SourcePurpose,
        customer_profile_id: uuid.UUID | None,
    ) -> SourceImportResult:
        # Serialize imports of one URL even when callers use different idempotency keys.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.sources.workspace_id}:{url}"},
        )
        source = await self.sources.get_by_source_url(url)
        if source is None:
            source = await self.sources.create(
                customer_profile_id=customer_profile_id,
                imported_by_id=self.user_id,
                type=SourceType.FEISHU_DOC,
                purpose=purpose,
                title="待同步飞书文档",
                source_url=url,
                tags=[],
                status=SourceStatus.PENDING,
            )
        else:
            await self.sources.update(
                source,
                customer_profile_id=customer_profile_id,
                imported_by_id=self.user_id,
                purpose=purpose,
                status=SourceStatus.PENDING,
            )

        job = await self._create_job(source.id)
        await self.session.commit()
        await self.session.refresh(source)
        await self.session.refresh(job)
        return SourceImportResult(source, job)

    async def import_text(
        self,
        *,
        title: str,
        content: str,
        purpose: SourcePurpose,
        customer_profile_id: uuid.UUID | None,
        is_demo: bool = False,
    ) -> SourceImportResult:
        source = await self.sources.create(
            customer_profile_id=customer_profile_id,
            imported_by_id=self.user_id,
            type=SourceType.PASTED_TEXT,
            purpose=purpose,
            title=title,
            content=content,
            source_url=None,
            tags=[],
            status=SourceStatus.PENDING,
            is_demo=is_demo,
        )
        job = await self._create_job(source.id)
        await self.session.commit()
        await self.session.refresh(source)
        await self.session.refresh(job)
        return SourceImportResult(source, job)

    async def list_sources(
        self,
        *,
        page: int,
        page_size: int,
        keyword: str | None = None,
        status: SourceStatus | None = None,
        purpose: SourcePurpose | None = None,
    ) -> tuple[list[Source], int]:
        status_value = status.value if status else None
        purpose_value = purpose.value if purpose else None
        items = await self.sources.list_filtered(
            offset=(page - 1) * page_size,
            limit=page_size,
            keyword=keyword,
            status=status_value,
            purpose=purpose_value,
        )
        total = await self.sources.count_filtered(
            keyword=keyword, status=status_value, purpose=purpose_value
        )
        return items, total

    async def get(self, source_id: uuid.UUID) -> Source:
        source = await self.sources.get(source_id)
        if source is None:
            raise AppError(ErrorCode.SOURCE_NOT_FOUND, "资料不存在", status_code=404)
        return source

    async def update(
        self,
        source_id: uuid.UUID,
        *,
        title: str | None,
        purpose: SourcePurpose | None,
        tags: list[str] | None,
    ) -> tuple[Source, Job | None]:
        source = await self.get(source_id)
        job = None
        if title is not None:
            source.title = title.strip()
        if tags is not None:
            source.tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if purpose is not None and purpose != source.purpose:
            await self._suspend_assets(source.id)
            source.purpose = purpose
            source.status = SourceStatus.PENDING
            job = await self._create_job(source.id)
        await self.session.commit()
        await self.session.refresh(source)
        return source, job

    async def refresh(self, source_id: uuid.UUID) -> SourceImportResult:
        source = await self.get(source_id)
        if source.type != SourceType.FEISHU_DOC or not source.source_url:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "只有飞书链接资料支持刷新",
                status_code=422,
            )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.sources.workspace_id}:refresh:{source.source_url}"},
        )
        canonical_url = source.source_url
        source.source_url = f"{canonical_url}#archived-v{source.content_version}"
        source.freshness_status = SourceFreshness.UPDATED
        await self._suspend_assets(source.id)
        replacement = await self.sources.create(
            previous_version_id=source.id,
            customer_profile_id=source.customer_profile_id,
            imported_by_id=self.user_id,
            type=source.type,
            purpose=source.purpose,
            title=source.title,
            source_url=canonical_url,
            author=source.author,
            tags=source.tags,
            status=SourceStatus.PENDING,
            freshness_status=SourceFreshness.CURRENT,
            content_version=source.content_version + 1,
        )
        job = await self._create_job(replacement.id)
        await self.session.commit()
        await self.session.refresh(replacement)
        await self.session.refresh(job)
        return SourceImportResult(replacement, job)

    async def retry(self, source_id: uuid.UUID) -> SourceImportResult:
        source = await self.get(source_id)
        if source.status != SourceStatus.FAILED:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "只有失败的资料处理任务可以重试",
                status_code=409,
            )
        source.status = SourceStatus.PENDING
        job = await self._create_job(source.id)
        await self.session.commit()
        await self.session.refresh(source)
        await self.session.refresh(job)
        return SourceImportResult(source, job)

    async def delete(self, source_id: uuid.UUID) -> None:
        source = await self.get(source_id)
        await self._suspend_assets(source.id)
        source.freshness_status = SourceFreshness.DELETED
        await self.sources.soft_delete(source)
        await self.session.commit()

    async def _suspend_assets(self, source_id: uuid.UUID) -> None:
        for model in (Experience, Capability):
            items = await self.session.scalars(
                select(model).where(
                    model.workspace_id == self.sources.workspace_id,
                    model.source_id == source_id,
                    model.is_deleted.is_(False),
                )
            )
            for asset in items:
                asset.review_status = ReviewStatus.SOURCE_UPDATED
                asset.embedding_ready = False

    async def _create_job(self, source_id: uuid.UUID) -> Job:
        return await self.jobs.create(
            type=JobType.SOURCE_PROCESSING,
            target_id=source_id,
            status=ProcessStatus.PENDING,
            stage="pending",
            retry_count=0,
        )
