import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.db.database import get_session_factory
from app.db.models import (
    RawArtifact,
    SearchRun,
    SearchRunStatus,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.integrations.http_open_enrich_adapter import HttpOpenEnrichAdapter
from app.integrations.protocols import IntelligenceProvider
from app.integrations.web_scraper import WebScraperAdapter
from app.schemas.intelligence_provider import EnrichmentJobRequest
from app.services.intelligence_service import IntelligenceService

logger = logging.getLogger(__name__)


def get_intelligence_provider() -> IntelligenceProvider:
    if settings.open_enrich_svc_url:
        return HttpOpenEnrichAdapter(
            settings.open_enrich_svc_url, token=settings.open_enrich_svc_token
        )
    raise AppError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "Open Enrich service is not configured; task skipped without mock output",
        status_code=503,
        retryable=False,
    )


async def run_open_enrich_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    provider: IntelligenceProvider | None = None,
) -> None:
    if provider is None:
        try:
            provider = get_intelligence_provider()
        except AppError as exc:
            await _mark_provider_unavailable(task_id, workspace_id, run_id, exc)
            return
    try:
        await _run_task(task_id, workspace_id, run_id, provider)
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()


async def _mark_provider_unavailable(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    error: AppError,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        task, run = await _load(session, task_id, workspace_id, run_id)
        if task is None or run is None:
            return
        _fail(task, run, error.code.value, error.message)
        await session.commit()


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
        if task.status in {WorkflowTaskStatus.COMPLETED, WorkflowTaskStatus.FAILED}:
            _log_terminal_task(task)


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
    limit_error = _limit_error(request, status.cost_usd, status.tool_calls_used)
    if limit_error:
        await _cancel_quietly(provider, provider_job_id)
        _fail(task, run, limit_error, "Provider execution limit reached")
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
    limit_error = _limit_error(request, result.cost_usd, result.tool_calls_used)
    if limit_error:
        await _cancel_quietly(provider, provider_job_id)
        _fail(task, run, limit_error, "Provider execution limit reached")
        return
    service = IntelligenceService(session, task.workspace_id, user_id)
    # Provider citations are discovery hints, not evidence. Re-fetch every new
    # public URL through the SSRF-safe scraper and persist its immutable body
    # before quote verification is allowed to run.
    await _capture_provider_sources(
        session,
        service=service,
        run=run,
        result=result,
        provider_job_id=provider_job_id,
    )
    # This service commits the verified intelligence records atomically.
    item, snapshot, proposal = await service.accept_provider_result(
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
    previous_summary = dict(run.result_summary or {})
    run.status = _final_run_status(result.status, previous_summary)
    open_enrich_summary = {
        "intelligence_item_id": str(item.id),
        "snapshot_id": str(snapshot.id),
        "proposal_id": str(proposal.id) if proposal else None,
        "fact_count": len(result.facts),
        "status": result.status,
        "cost_usd": result.cost_usd,
        "tool_calls_used": result.tool_calls_used,
    }
    if previous_summary:
        run.result_summary = {
            **previous_summary,
            "open_enrich": open_enrich_summary,
        }
    else:
        run.result_summary = {
            **open_enrich_summary,
            "provider_chain": ["open_enrich"],
            "open_enrich": open_enrich_summary,
        }
    run.completed_at = datetime.now(UTC)


async def _capture_provider_sources(
    session,
    *,
    service: IntelligenceService,
    run,
    result,
    provider_job_id: str,
) -> None:
    rows = (
        await session.execute(
            select(RawArtifact.source_url, RawArtifact.normalized_url).where(
                RawArtifact.workspace_id == run.workspace_id,
                RawArtifact.search_run_id == run.id,
                RawArtifact.is_deleted.is_(False),
            )
        )
    ).all()
    known_urls = {
        alias
        for row in rows
        for value in row
        if value
        for alias in _url_aliases(str(value))
    }
    citation_urls: list[str] = []
    for fact in result.facts:
        for citation in fact.citations:
            url = str(citation.url)
            if _url_aliases(url).isdisjoint(known_urls) and url not in citation_urls:
                citation_urls.append(url)

    if not citation_urls:
        return
    scraper = WebScraperAdapter()
    captures = [await scraper.fetch(url) for url in citation_urls]
    await service.persist_provider_captures(
        run_id=run.id,
        captures=captures,
        provider_job_id=provider_job_id,
    )


def _url_aliases(value: str) -> set[str]:
    stripped = value.strip()
    without_slash = stripped.rstrip("/")
    return {stripped, without_slash, f"{without_slash}/"}


def _final_run_status(provider_status: str, previous_summary: dict) -> SearchRunStatus:
    primary_had_warnings = bool(
        previous_summary.get("skipped")
        or previous_summary.get("enrichment_error_count")
    )
    if provider_status == "partial" or primary_had_warnings:
        return SearchRunStatus.PARTIAL
    return SearchRunStatus.COMPLETED


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


def _limit_error(
    request: EnrichmentJobRequest, cost_usd: float, tool_calls: int
) -> str | None:
    if tool_calls >= request.max_tool_calls:
        return "TOOL_CALL_LIMIT_EXCEEDED"
    if cost_usd >= request.max_cost_usd:
        return ErrorCode.QUOTA_EXCEEDED.value
    return None


async def _cancel_quietly(provider, provider_job_id) -> None:
    if not provider_job_id:
        return
    try:
        await provider.cancel_job(str(provider_job_id))
    except Exception:
        logger.warning(
            "Open Enrich cancellation failed",
            extra={"provider_job_id": str(provider_job_id)},
        )


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
    previous_summary = dict(getattr(run, "result_summary", {}) or {})
    provider_chain = previous_summary.get("provider_chain") or []
    has_primary_result = (
        previous_summary.get("provider") == "bailian_web_search"
        or "bailian_web_search" in provider_chain
    )
    if has_primary_result:
        run.status = SearchRunStatus.PARTIAL
        run.error_code = None
        run.error_summary = None
        run.result_summary = {
            **previous_summary,
            "open_enrich": {
                "status": "failed",
                "error_code": code[:64],
                "error_summary": summary[:1000],
            },
        }
    else:
        run.status = SearchRunStatus.FAILED
        run.error_code = code[:64]
        run.error_summary = summary[:1000]
    run.completed_at = now


def _log_terminal_task(task) -> None:
    payload = task.payload if isinstance(task.payload, dict) else {}
    started_at = getattr(task, "created_at", None)
    finished_at = task.finished_at or datetime.now(UTC)
    duration_ms = None
    if started_at is not None:
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    event = {
        "event": "open_enrich_task_terminal",
        "task_id": str(task.id),
        "provider_job_id": str(payload.get("provider_job_id") or ""),
        "status": task.status.value,
        "error_code": task.error_code,
        "cost_usd": float(payload.get("cost_usd") or 0),
        "tool_calls": int(payload.get("tool_calls_used") or 0),
        "duration_ms": duration_ms,
    }
    logger.info(json.dumps(event, separators=(",", ":"), ensure_ascii=True))
