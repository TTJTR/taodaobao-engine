import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, text

from app.api.deps import get_ai_engine, get_embedding_provider
from app.db.database import get_session_factory
from app.db.models import (
    MessageRole,
    ProcessStatus,
    SolutionRun,
    TrustAction,
    TrustDecisionRecord,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.db.repositories import MessageRepository
from app.services.solution_pipeline import run_solution_pipeline
from app.services.trust_gate import GATE_POLICY_VERSION, THRESHOLD_VERSION

logger = logging.getLogger(__name__)
# External provider calls are bounded at 120 seconds. Keep the lease valid for
# the whole call so a second worker cannot reclaim the same task mid-render.
LEASE_SECONDS = 360
RETRYABLE_CODES = {
    "MODEL_TEMPORARILY_UNAVAILABLE",
    "VERIFIER_UNAVAILABLE",
    "INTERNAL_ERROR",
    "PRESENTATION_RENDER_FAILED",
    "TENDER_PARSE_TIMEOUT",
}


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def claim_next_task(worker_id: str) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID] | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        now = datetime.now(UTC)
        statement = (
            select(WorkflowTask)
            .where(
                WorkflowTask.is_deleted.is_(False),
                WorkflowTask.attempt_count < WorkflowTask.max_attempts,
                WorkflowTask.available_at <= now,
                or_(
                    WorkflowTask.status == WorkflowTaskStatus.QUEUED,
                    (
                        (WorkflowTask.status == WorkflowTaskStatus.RUNNING)
                        & (WorkflowTask.lease_expires_at < now)
                    ),
                ),
            )
            .order_by(WorkflowTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        task = await session.scalar(statement)
        if task is None:
            return None
        task.status = WorkflowTaskStatus.RUNNING
        task.attempt_count += 1
        task.lease_owner = worker_id
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        await session.commit()
        return task.id, task.workspace_id, task.kind, task.target_id


async def process_claimed_task(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kind: str,
    target_id: uuid.UUID,
) -> None:
    if kind == "solution_run":
        await run_solution_pipeline(
            target_id,
            workspace_id,
            get_ai_engine(),
            get_embedding_provider(),
            task_id=task_id,
        )
    elif kind == "tender_parse":
        from app.services.tender_task_worker import run_tender_parse_task

        await run_tender_parse_task(task_id, workspace_id, target_id)
    else:
        from app.services.presentation_worker import run_presentation_task

        await run_presentation_task(task_id, workspace_id, kind, target_id)
    await _reschedule_retryable_failure(task_id, workspace_id)


async def _reschedule_retryable_failure(task_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.workspace_id == workspace_id,
                WorkflowTask.is_deleted.is_(False),
            ).with_for_update()
        )
        if task is None or task.status != WorkflowTaskStatus.FAILED:
            return
        if task.error_code not in RETRYABLE_CODES:
            return
        exhausted = task.attempt_count >= task.max_attempts or bool(
            task.deadline_at and task.deadline_at <= datetime.now(UTC)
        )
        if exhausted:
            if task.kind == "solution_run":
                await _finalize_exhausted_solution(session, task)
            return
        task.status = WorkflowTaskStatus.QUEUED
        task.stage = "queued_retry"
        task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempt_count)
        task.finished_at = None
        await session.commit()


async def _finalize_exhausted_solution(session, task: WorkflowTask) -> None:
    run = await session.scalar(
        select(SolutionRun).where(
            SolutionRun.id == task.target_id,
            SolutionRun.workspace_id == task.workspace_id,
            SolutionRun.is_deleted.is_(False),
        )
    )
    if run is None:
        return
    latest = await session.scalar(
        select(TrustDecisionRecord)
        .where(
            TrustDecisionRecord.solution_run_id == run.id,
            TrustDecisionRecord.workspace_id == task.workspace_id,
            TrustDecisionRecord.is_deleted.is_(False),
        )
        .order_by(TrustDecisionRecord.version.desc())
        .limit(1)
    )
    if latest is None or "BE05_RETRY_BUDGET_EXHAUSTED" not in latest.reason_codes:
        session.add(
            TrustDecisionRecord(
                workspace_id=task.workspace_id,
                solution_run_id=run.id,
                version=(latest.version + 1 if latest else 1),
                action=TrustAction.REVIEW,
                reason_codes=["BE05_RETRY_BUDGET_EXHAUSTED", task.error_code or "UNKNOWN"],
                gate_policy_version=GATE_POLICY_VERSION,
                threshold_version=THRESHOLD_VERSION,
                decided_by="system",
                decision_details={
                    "attempt_count": task.attempt_count,
                    "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
                    "failed_stage": task.stage,
                },
            )
        )
    run.status = ProcessStatus.COMPLETED
    run.stage = "completed"
    run.result = _review_placeholder()
    run.display_text = "方案核验预算已耗尽，已安全转入人工审核，未发布任何企业结论。"
    run.error_code = "TRUST_REVIEW_REQUIRED"
    run.retryable = False
    run.completed_at = datetime.now(UTC)
    task.status = WorkflowTaskStatus.COMPLETED
    task.stage = "completed_review"
    task.finished_at = datetime.now(UTC)
    task.lease_owner = None
    task.lease_expires_at = None
    messages = MessageRepository(session, task.workspace_id)
    if await messages.get_for_solution_run(run.id) is None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{task.workspace_id}:session-sequence:{run.session_id}"},
        )
        await messages.create(
            session_id=run.session_id,
            solution_run_id=run.id,
            role=MessageRole.ASSISTANT,
            content=run.display_text,
            sequence=await messages.next_sequence(run.session_id),
        )
    await session.commit()


def _review_placeholder() -> dict:
    return {
        "requirement_understanding": [
            {
                "text": "本次核验在统一预算内未完成，不能作为正式企业结论使用。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [],
        "historical_evidence": [],
        "capability_composition": [],
        "prerequisites_and_risks": [],
        "pending_confirmations": [
            {
                "text": "请人工复核证据，或在新的运行中重新生成。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "sources": [],
        "suggested_questions": ["是否需要重新运行核验或提交人工审核？"],
    }


async def run_worker(*, poll_seconds: float = 0.5, once: bool = False) -> None:
    worker_id = worker_identity()
    logger.info("durable worker started: %s", worker_id)
    while True:
        claimed = await claim_next_task(worker_id)
        if claimed is None:
            if once:
                return
            await asyncio.sleep(poll_seconds)
            continue
        try:
            await process_claimed_task(*claimed)
        except Exception:
            logger.exception("workflow task crashed: %s", claimed[0])
        if once:
            return
