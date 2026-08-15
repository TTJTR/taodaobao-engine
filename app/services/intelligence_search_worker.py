import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import AppError, ErrorCode
from app.db.database import get_session_factory
from app.db.models import SearchRun, SearchRunStatus, WorkflowTask, WorkflowTaskStatus
from app.integrations.protocols import SearchProvider
from app.integrations.web_scraper import WebScraperAdapter
from app.schemas.intelligence_provider import EnrichmentJobRequest
from app.services.intelligence_service import IntelligenceService
from app.services.model_connection_service import workspace_ai_engine, workspace_search_provider

logger = logging.getLogger(__name__)


async def run_intelligence_search_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    provider: SearchProvider | None = None,
    scraper: WebScraperAdapter | None = None,
) -> None:
    session_factory = get_session_factory()
    owns_provider = provider is None
    try:
        async with session_factory() as session:
            task, run = await _load(session, task_id, workspace_id, run_id, lock=True)
            if task is None or run is None:
                return
            context = _context(task.payload)
            if task.deadline_at and task.deadline_at <= datetime.now(UTC):
                _fail(task, run, "PROVIDER_TIMEOUT", "公开情报搜索任务已超过截止时间")
                await session.commit()
                return
            if provider is None:
                provider = await workspace_search_provider(session, workspace_id)
            if provider is None:
                _fail(
                    task,
                    run,
                    ErrorCode.PROVIDER_UNAVAILABLE.value,
                    "未配置阿里云百炼公开搜索；任务已跳过且未生成 Mock 结果",
                )
                await session.commit()
                return
            task.status = WorkflowTaskStatus.RUNNING
            task.stage = "discovering_sources"
            run.status = SearchRunStatus.RUNNING
            await session.commit()

        result = await provider.search(
            context["query"],
            max_results=context["max_results"],
            language=context["language"],
            country=context["country"],
        )
        scraper = scraper or WebScraperAdapter()
        captures = []
        skipped = []
        for source in result.sources:
            try:
                captures.append((source, await scraper.fetch(str(source.url))))
            except AppError as exc:
                skipped.append(
                    {
                        "url": str(source.url),
                        "code": exc.code.value,
                        "reason": exc.message,
                    }
                )
            except Exception as exc:
                skipped.append(
                    {
                        "url": str(source.url),
                        "code": ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE.value,
                        "reason": type(exc).__name__,
                    }
                )
        if not captures:
            raise AppError(
                ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE,
                "搜索返回了来源，但没有任何公开正文能够通过安全抓取",
                status_code=503,
                retryable=True,
                details={"skipped_count": len(skipped)},
            )

        async with session_factory() as session:
            service = IntelligenceService(session, workspace_id, context["user_id"])
            artifacts, items = await service.accept_discovered_sources(
                run_id=run_id,
                sources=captures,
                provider_request_id=result.provider_request_id,
            )
            snapshot = await service.create_snapshot(
                context["purpose"], [item.id for item in items]
            )
            proposal_ids: list[str] = []
            enrichment_errors: list[dict[str, str]] = []
            if context["profile_id"] is not None:
                try:
                    ai_engine = await workspace_ai_engine(session, workspace_id)
                    for artifact in artifacts:
                        try:
                            _, _, proposal = await service.enrich_artifact_for_profile(
                                artifact.id,
                                context["profile_id"],
                                ai_engine,
                            )
                            if proposal is not None:
                                proposal_ids.append(str(proposal.id))
                        except Exception as exc:
                            enrichment_errors.append(
                                {"artifact_id": str(artifact.id), "reason": type(exc).__name__}
                            )
                except Exception as exc:
                    enrichment_errors.append({"artifact_id": "*", "reason": type(exc).__name__})

            task, run = await _load(session, task_id, workspace_id, run_id, lock=True)
            if task is None or run is None:
                return
            follow_up_request = context["open_enrich_request"]
            partial = bool(skipped or enrichment_errors)
            task.payload = {
                **task.payload,
                "provider_request_id": result.provider_request_id,
                "source_count": len(result.sources),
                "captured_count": len(artifacts),
                "skipped_sources": skipped,
                "snapshot_id": str(snapshot.id),
                "proposal_ids": proposal_ids,
                "enrichment_errors": enrichment_errors,
                "usage": result.usage,
            }
            task.status = WorkflowTaskStatus.COMPLETED
            task.stage = (
                "completed_primary"
                if follow_up_request is not None
                else ("completed_with_warnings" if partial else "completed")
            )
            task.finished_at = datetime.now(UTC)
            task.error_code = None
            task.error_summary = None
            task.lease_owner = None
            task.lease_expires_at = None
            run.status = (
                SearchRunStatus.QUEUED
                if follow_up_request is not None
                else (SearchRunStatus.PARTIAL if partial else SearchRunStatus.COMPLETED)
            )
            run.result_summary = {
                "provider": result.provider,
                "provider_chain": (
                    ["bailian_web_search", "open_enrich"]
                    if follow_up_request is not None
                    else ["bailian_web_search"]
                ),
                "provider_request_id": result.provider_request_id,
                "discovered": len(result.sources),
                "captured": len(artifacts),
                "skipped": len(skipped),
                "snapshot_id": str(snapshot.id),
                "proposal_ids": proposal_ids,
                "enrichment_error_count": len(enrichment_errors),
                "trust_boundary": "external_public_information",
            }
            run.completed_at = None if follow_up_request is not None else datetime.now(UTC)
            await session.commit()
            if follow_up_request is not None:
                try:
                    follow_up_task = await service.queue_provider_enrichment(
                        run.id,
                        context["profile_id"],
                        follow_up_request,
                    )
                    task, run = await _load(session, task_id, workspace_id, run_id, lock=True)
                    if task is not None and run is not None:
                        task.payload = {
                            **task.payload,
                            "open_enrich_task_id": str(follow_up_task.id),
                        }
                        await session.commit()
                except Exception as exc:
                    task, run = await _load(session, task_id, workspace_id, run_id, lock=True)
                    if task is not None and run is not None:
                        task.stage = "completed_with_warnings"
                        run.status = SearchRunStatus.PARTIAL
                        run.completed_at = datetime.now(UTC)
                        run.result_summary = {
                            **dict(run.result_summary or {}),
                            "open_enrich": {
                                "status": "not_started",
                                "error_code": (
                                    exc.code.value
                                    if isinstance(exc, AppError)
                                    else ErrorCode.INTERNAL_ERROR.value
                                ),
                            },
                        }
                        await session.commit()
            _log_terminal(task, run)
    except Exception as exc:
        async with session_factory() as session:
            task, run = await _load(session, task_id, workspace_id, run_id, lock=True)
            if task is not None and run is not None:
                code = (
                    exc.code.value
                    if isinstance(exc, AppError)
                    else ErrorCode.INTERNAL_ERROR.value
                )
                summary = exc.message if isinstance(exc, AppError) else "公开情报搜索执行失败"
                _fail(task, run, code, summary)
                await session.commit()
                _log_terminal(task, run)
    finally:
        if owns_provider and provider is not None:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()


async def _load(session, task_id, workspace_id, run_id, *, lock: bool):
    task_query = select(WorkflowTask).where(
        WorkflowTask.id == task_id,
        WorkflowTask.workspace_id == workspace_id,
        WorkflowTask.kind == "intelligence_search",
        WorkflowTask.target_id == run_id,
        WorkflowTask.is_deleted.is_(False),
    )
    if lock:
        task_query = task_query.with_for_update()
    task = await session.scalar(task_query)
    run_query = select(SearchRun).where(
        SearchRun.id == run_id,
        SearchRun.workspace_id == workspace_id,
        SearchRun.is_deleted.is_(False),
    )
    if lock:
        run_query = run_query.with_for_update()
    return task, await session.scalar(run_query)


def _context(payload: dict) -> dict:
    try:
        return {
            "profile_id": (
                uuid.UUID(payload["profile_id"]) if payload.get("profile_id") else None
            ),
            "user_id": uuid.UUID(payload["user_id"]),
            "query": str(payload["query"]),
            "purpose": str(payload["purpose"]),
            "max_results": int(payload["max_results"]),
            "language": str(payload.get("language") or "zh-CN"),
            "country": str(payload["country"]) if payload.get("country") else None,
            "open_enrich_request": (
                EnrichmentJobRequest.model_validate(payload["open_enrich_request"])
                if payload.get("open_enrich_request")
                else None
            ),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "公开情报搜索任务上下文无效",
            status_code=422,
        ) from exc


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


def _log_terminal(task, run) -> None:
    logger.info(
        json.dumps(
            {
                "event": "intelligence_search_task_terminal",
                "task_id": str(task.id),
                "run_id": str(run.id),
                "status": task.status.value,
                "stage": task.stage,
                "error_code": task.error_code,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
