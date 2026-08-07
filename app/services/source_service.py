import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Job,
    JobType,
    ProcessStatus,
    Source,
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
    ) -> tuple[list[Source], int]:
        status_value = status.value if status else None
        items = await self.sources.list_filtered(
            offset=(page - 1) * page_size,
            limit=page_size,
            keyword=keyword,
            status=status_value,
        )
        total = await self.sources.count_filtered(keyword=keyword, status=status_value)
        return items, total

    async def _create_job(self, source_id: uuid.UUID) -> Job:
        return await self.jobs.create(
            type=JobType.SOURCE_PROCESSING,
            target_id=source_id,
            status=ProcessStatus.PENDING,
            stage="pending",
            retry_count=0,
        )
