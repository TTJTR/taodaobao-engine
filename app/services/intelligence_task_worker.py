import logging
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.db.database import get_session_factory
from app.db.models import SearchRun, SearchRunStatus, WorkflowTask, WorkflowTaskStatus
from app.integrations.http_open_enrich_adapter import HttpOpenEnrichAdapter
from app.integrations.open_enrich_adapter import OpenEnrichAdapter
from app.integrations.protocols import IntelligenceProvider
from app.schemas.intelligence_provider import EnrichmentJobRequest
from app.services.intelligence_service import IntelligenceService

logger = logging.getLogger(__name__)
_mock_provider = OpenEnrichAdapter()


def get_intelligence_provider() -> IntelligenceProvider:
    if settings.open_enrich_svc_url:
        return HttpOpenEnrichAdapter(
            settings.open_enrich_svc_url, token=settings.open_enrich_svc_token
        )
    return _mock_provider


async def run_open_enrich_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    provider: IntelligenceProvider | None = None,
) -> None:
    provider = provider or get_intelligence_provider()
    try:
        await _run_task(task_id, workspace_id, run_id, provider)
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()


async def _run_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    provider: IntelligenceProvider,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        task, run = await _load(session, task_id, workspace_id, run_id)
        if task is None or run is None:
            return
        try:
            request, profile_id, user_id = _context(task.payload)
            if task.deadline_at and task.deadline_at <= datetime.now(UTC):
                await _cancel_quietly(provider, task.payload.get("provider_job_id"))
                _fail(task, run, "PROVIDER_TIMEOUT", "Open Enrich task deadline exceeded")
            elif not task.payload.get("provider_job_id"):
                accepted = await provider.submit_job(request)
                task.payload = {**task.payload, "provider_job_id": accepted.provider_job_id}
                run.status = SearchRunStatus.RUNNING
                _requeue(task, "polling")
            else:
                await _poll_once(
                    session, task, run, request, profile_id, user_id, provider
                )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            task, run = await _load(session, task_id, workspace_id, run_id)
            if task is None or run is None:
                return
            code = exc.code.value if isinstance(exc, AppError) else ErrorCode.INTERNAL_ERROR.value
            summary = exc.message if isinstance(exc, AppError) else str(exc)
            _fail(task, run, code, summary)
            await session.commit()


async def _poll_once(session, task, run, request, profile_id, user_id, provider) -> None:
    provider_job_id = str(task.payload["provider_job_id"])
    status = await provider.get_job_status(provider_job_id)
    if status.provider_job_id != provider_job_id:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Open Enrich returned a mismatched job ID",
            status_code=502,
            retryable=True,
        )
    task.payload = {
        **task.payload,
        "poll_count": int(task.payload.get("poll_count", 0)) + 1,
        "cost_usd": status.cost_usd,
        "tool_calls_used": status.tool_calls_used,
    }
    if _over_quota(request, status.cost_usd, status.tool_calls_used):
        await _cancel_quietly(provider, provider_job_id)
        _fail(task, run, ErrorCode.QUOTA_EXCEEDED.value, "Provider quota exceeded")
        return
    if status.status in {"queued", "processing"}:
        run.status = SearchRunStatus.RUNNING
        _requeue(task, "polling")
        return
    if status.status == "failed":
        _fail(
            task,
            run,
            status.error_code or "PROVIDER_FAILED",
            status.error_summary or "Open Enrich provider failed",
        )
        return
    result = await provider.fetch_results(provider_job_id)
    if result.provider_job_id != provider_job_id:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Open Enrich returned a mismatched result job ID",
            status_code=502,
            retryable=True,
        )
    if _over_quota(request, result.cost_usd, result.tool_calls_used):
        await _cancel_quietly(provider, provider_job_id)
        _fail(task, run, ErrorCode.QUOTA_EXCEEDED.value, "Provider quota exceeded")
        return
    # This service commits the verified intelligence records atomically.
    item, snapshot, proposal = await IntelligenceService(
        session, task.workspace_id, user_id
    ).accept_provider_result(
        run_id=run.id,
        profile_id=profile_id,
        request=request,
        result=result,
    )
    task, run = await _load(session, task.id, task.workspace_id, run.id)
    if task is None or run is None:
        return
    task.payload = {
        **task.payload,
        "cost_usd": result.cost_usd,
        "tool_calls_used": result.tool_calls_used,
        "intelligence_item_id": str(item.id),
        "snapshot_id": str(snapshot.id),
        "proposal_id": str(proposal.id) if proposal else None,
    }
    task.status = WorkflowTaskStatus.COMPLETED
    task.stage = "completed"
    task.finished_at = datetime.now(UTC)
    task.error_code = None
    task.error_summary = None
    task.lease_owner = None
    task.lease_expires_at = None
    run.status = (
        SearchRunStatus.PARTIAL
        if result.status == "partial"
        else SearchRunStatus.COMPLETED
    )
    run.result_summary = {
        "intelligence_item_id": str(item.id),
        "snapshot_id": str(snapshot.id),
        "proposal_id": str(proposal.id) if proposal else None,
        "fact_count": len(result.facts),
    }
    run.completed_at = datetime.now(UTC)


async def _load(session, task_id, workspace_id, run_id):
    task = await session.scalar(
        select(WorkflowTask).where(
            WorkflowTask.id == task_id,
            WorkflowTask.workspace_id == workspace_id,
            WorkflowTask.kind == "open_enrich",
            WorkflowTask.target_id == run_id,
            WorkflowTask.is_deleted.is_(False),
        ).with_for_update()
    )
    run = await session.scalar(
        select(SearchRun).where(
            SearchRun.id == run_id,
            SearchRun.workspace_id == workspace_id,
            SearchRun.is_deleted.is_(False),
        )
    )
    return task, run


def _context(payload):
    try:
        return (
            EnrichmentJobRequest.model_validate(payload["request"]),
            uuid.UUID(str(payload["profile_id"])),
            uuid.UUID(str(payload["user_id"])),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise AppError(ErrorCode.VALIDATION_FAILED, "Invalid Open Enrich task payload") from exc


def _over_quota(request: EnrichmentJobRequest, cost_usd: float, tool_calls: int) -> bool:
    return cost_usd > request.max_cost_usd or tool_calls > request.max_tool_calls


async def _cancel_quietly(provider, provider_job_id) -> None:
    if not provider_job_id:
        return
    try:
        await provider.cancel_job(str(provider_job_id))
    except Exception:
        logger.warning("Open Enrich cancellation failed for %s", provider_job_id, exc_info=True)


def _requeue(task, stage: str) -> None:
    task.status = WorkflowTaskStatus.QUEUED
    task.stage = stage
    task.available_at = datetime.now(UTC) + timedelta(seconds=settings.open_enrich_poll_seconds)
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = None
    task.error_code = None
    task.error_summary = None


def _fail(task, run, code: str, summary: str) -> None:
    now = datetime.now(UTC)
    task.status = WorkflowTaskStatus.FAILED
    task.stage = "failed"
    task.error_code = code[:64]
    task.error_summary = summary[:1000]
    task.finished_at = now
    task.lease_owner = None
    task.lease_expires_at = None
    run.status = SearchRunStatus.FAILED
    run.error_code = code[:64]
    run.error_summary = summary[:1000]
    run.completed_at = now
