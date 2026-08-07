import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import ProcessStatus, SourceStatus, SourceType
from app.db.repositories import JobRepository, SourceRepository
from app.integrations import FeishuAdapter

STAGE_DELAY_SECONDS = 0.05


async def run_source_job(
    job_id: uuid.UUID,
    source_id: uuid.UUID,
    workspace_id: uuid.UUID,
    feishu_adapter: FeishuAdapter,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        jobs = JobRepository(session, workspace_id)
        sources = SourceRepository(session, workspace_id)
        job = await jobs.get(job_id)
        source = await sources.get(source_id)
        if job is None or source is None:
            return

        try:
            await _set_stage(session, job, source, "fetching", SourceStatus.FETCHING)
            if source.type == SourceType.FEISHU_DOC:
                document = await feishu_adapter.fetch_document(source.source_url or "")
                source.title = document.title
                source.content = document.content
                source.author = document.author
                source.source_url = document.source_url
                source.source_updated_at = _parse_datetime(document.source_updated_at)
                source.synced_at = datetime.now(UTC)
                await session.commit()

            await asyncio.sleep(STAGE_DELAY_SECONDS)
            await _set_stage(session, job, source, "parsing", SourceStatus.PARSING)
            await asyncio.sleep(STAGE_DELAY_SECONDS)
            await _set_stage(session, job, source, "extracting", SourceStatus.EXTRACTING)
            await asyncio.sleep(STAGE_DELAY_SECONDS)

            source.status = SourceStatus.PENDING_REVIEW
            job.status = ProcessStatus.COMPLETED
            job.stage = "pending_review"
            job.finished_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            job = await jobs.get(job_id)
            source = await sources.get(source_id)
            if job is None or source is None:
                return
            source.status = SourceStatus.FAILED
            job.status = ProcessStatus.FAILED
            job.stage = "failed"
            job.error_code = ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE.value
            job.error_summary = str(exc)[:1000]
            job.retry_count += 1
            job.finished_at = datetime.now(UTC)
            await session.commit()


async def _set_stage(session, job, source, stage: str, source_status: SourceStatus) -> None:
    job.status = ProcessStatus.RUNNING
    job.stage = stage
    source.status = source_status
    await session.commit()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
