import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select, text

from app.ai.embedding import EmbeddingProvider
from app.contracts.ai import AIEngine
from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import (
    ClaimRecord,
    MessageRole,
    ProcessStatus,
    RetrievalSnapshotRecord,
    TrustAction,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.db.repositories import MessageRepository, SolutionRunRepository
from app.schemas.trust import SolutionV2Payload
from app.services.ai_payload_adapter import (
    build_solution_context,
    normalize_retrieval_snapshot,
    normalize_solution_v2_result,
)
from app.services.ai_run_service import persist_last_ai_run
from app.services.retrieval_service import RetrievalService
from app.services.trust_gate import TrustPersistenceService, build_safe_solution

SOLUTION_DEADLINE_SECONDS = 60


async def run_solution_pipeline(
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ai_engine: AIEngine,
    embedding_provider: EmbeddingProvider | None = None,
    *,
    task_id: uuid.UUID | None = None,
    delay_seconds: float = 0,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        runs = SolutionRunRepository(session, workspace_id)
        messages = MessageRepository(session, workspace_id)
        run = await runs.get(run_id)
        task = await _get_task(session, workspace_id, task_id)
        if run is None or run.status == ProcessStatus.COMPLETED:
            await _finish_task(session, task)
            return
        try:
            now = datetime.now(UTC)
            run.status = ProcessStatus.RUNNING
            run.deadline_at = run.deadline_at or now + timedelta(seconds=SOLUTION_DEADLINE_SECONDS)
            run.attempt_count += 1
            await _set_stage(session, run, task, "retrieving")
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            request_message = await messages.get(run.request_message_id)
            if request_message is None:
                raise RuntimeError("request message is missing")

            solution_context = build_solution_context(request_message.content, run.profile_snapshot)
            external_context = (run.retrieval_snapshot or {}).get("external_context")
            if external_context:
                solution_context["external_context"] = external_context
            solution_context.update(
                {
                    "schema_version": "solution-v2",
                    "trace_id": run.trace_id,
                    "deadline_at": run.deadline_at.isoformat(),
                    "retry_budget": max(0, 3 - run.attempt_count),
                }
            )
            conversation = await messages.list_for_session(run.session_id)
            solution_context["conversation_history"] = [
                {"role": item.role.value, "content": item.content} for item in conversation[-20:]
            ]
            remaining = _remaining_seconds(run.deadline_at)
            async with asyncio.timeout(remaining):
                raw_snapshot = await RetrievalService(session, workspace_id).retrieve(
                    request_message.content,
                    context=solution_context,
                    ai_engine=ai_engine,
                    embedding_provider=embedding_provider,
                )
            persist_last_ai_run(
                session,
                workspace_id,
                ai_engine,
                target_type="solution_run",
                target_id=run.id,
                input_summary={"stage": "search_intent"},
                trace_id=run.trace_id,
            )
            ai_snapshot = normalize_retrieval_snapshot(raw_snapshot)
            if external_context:
                ai_snapshot["external_context"] = external_context
            run.retrieval_snapshot = ai_snapshot
            session.add(
                RetrievalSnapshotRecord(
                    workspace_id=workspace_id,
                    solution_run_id=run.id,
                    version=run.result_version,
                    snapshot_data=raw_snapshot,
                    source_versions={
                        item["source_id"]: item.get("source_version")
                        for item in [
                            *raw_snapshot.get("experiences", []),
                            *raw_snapshot.get("capabilities", []),
                        ]
                    },
                    permission_snapshot={
                        item["source_id"]: {
                            "status": item.get("permission_status"),
                            "checked_at": item.get("permission_checked_at"),
                        }
                        for item in [
                            *raw_snapshot.get("experiences", []),
                            *raw_snapshot.get("capabilities", []),
                        ]
                    },
                    embedding_version=_embedding_version(raw_snapshot),
                )
            )
            await _set_stage(session, run, task, "generating")
            async with asyncio.timeout(_remaining_seconds(run.deadline_at)):
                candidate = await ai_engine.generate_solution(solution_context, ai_snapshot)
            await _set_stage(session, run, task, "verifying")
            payload = SolutionV2Payload.model_validate(normalize_solution_v2_result(candidate))
            persist_last_ai_run(
                session,
                workspace_id,
                ai_engine,
                target_type="solution_run",
                target_id=run.id,
                input_summary={"stage": "generate_verify_revise"},
                trace_id=run.trace_id,
            )

            await _set_stage(session, run, task, "gating")
            decision = await TrustPersistenceService(session, workspace_id).persist_and_gate(
                run, payload, raw_snapshot
            )
            claims = list(
                (
                    await session.scalars(
                        select(ClaimRecord).where(
                            ClaimRecord.workspace_id == workspace_id,
                            ClaimRecord.solution_run_id == run.id,
                            ClaimRecord.candidate_version == run.result_version,
                            ClaimRecord.is_deleted.is_(False),
                        )
                    )
                ).all()
            )
            run.result = build_safe_solution(payload, decision, claims)
            run.display_text = _display_text(decision.action)
            run.status = ProcessStatus.COMPLETED
            run.stage = "completed"
            run.error_code = (
                ErrorCode.TRUST_GATE_BLOCKED.value
                if decision.action == TrustAction.BLOCK
                else ErrorCode.TRUST_REVIEW_REQUIRED.value
                if decision.action == TrustAction.REVIEW
                else None
            )
            run.retryable = False
            run.completed_at = datetime.now(UTC)
            if await messages.get_for_solution_run(run.id) is None:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"{workspace_id}:session-sequence:{run.session_id}"},
                )
                await messages.create(
                    session_id=run.session_id,
                    solution_run_id=run.id,
                    role=MessageRole.ASSISTANT,
                    content=run.display_text,
                    sequence=await messages.next_sequence(run.session_id),
                )
            await _finish_task(session, task)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            run = await runs.get(run_id)
            task = await _get_task(session, workspace_id, task_id)
            if run is None:
                return
            error_code, retryable = _classify_error(exc, run.stage)
            run.status = ProcessStatus.FAILED
            run.stage = "failed"
            run.error_code = error_code.value
            run.retryable = retryable and run.attempt_count < 3
            run.completed_at = datetime.now(UTC)
            if task is not None:
                task.status = WorkflowTaskStatus.FAILED
                task.stage = "failed"
                task.error_code = error_code.value
                task.error_summary = type(exc).__name__
                task.finished_at = datetime.now(UTC)
                task.lease_owner = None
                task.lease_expires_at = None
            await session.commit()


async def _set_stage(session, run, task, stage: str) -> None:
    run.stage = stage
    if task is not None:
        task.stage = stage
        now = datetime.now(UTC)
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=180)
    await session.commit()


async def _get_task(session, workspace_id, task_id):
    if task_id is None:
        return None
    return await session.scalar(
        select(WorkflowTask).where(
            WorkflowTask.id == task_id,
            WorkflowTask.workspace_id == workspace_id,
            WorkflowTask.is_deleted.is_(False),
        )
    )


async def _finish_task(session, task) -> None:
    if task is None:
        return
    task.status = WorkflowTaskStatus.COMPLETED
    task.stage = "completed"
    task.finished_at = datetime.now(UTC)
    task.heartbeat_at = datetime.now(UTC)
    task.lease_owner = None
    task.lease_expires_at = None


def _remaining_seconds(deadline_at: datetime) -> float:
    remaining = (deadline_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("solution deadline exceeded")
    return remaining


def _classify_error(exc: Exception, stage: str) -> tuple[ErrorCode, bool]:
    if isinstance(exc, (ValidationError, ValueError)):
        return ErrorCode.AI_OUTPUT_INVALID, False
    if isinstance(exc, TimeoutError):
        return (
            ErrorCode.VERIFIER_UNAVAILABLE
            if stage in {"generating", "verifying"}
            else ErrorCode.MODEL_TEMPORARILY_UNAVAILABLE,
            True,
        )
    return ErrorCode.INTERNAL_ERROR, True


def _embedding_version(snapshot: dict) -> str | None:
    return next(
        (
            item.get("embedding_version")
            for item in [
                *snapshot.get("experiences", []),
                *snapshot.get("capabilities", []),
            ]
            if item.get("embedding_version")
        ),
        None,
    )


def _display_text(action: TrustAction) -> str:
    return {
        TrustAction.RELEASE: "快速方案已通过可信门禁，可按来源与边界使用。",
        TrustAction.DOWNGRADE: "快速方案已安全降级发布，高风险未核验内容已移除。",
        TrustAction.REVIEW: "快速方案已生成，但需要人工完成可信审核后才能正式使用。",
        TrustAction.BLOCK: "快速方案未通过可信门禁，已阻止正式发布。",
        TrustAction.PENDING: "快速方案正在执行可信检查。",
    }[action]
