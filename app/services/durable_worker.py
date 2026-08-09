import asyncio
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.api.deps import get_ai_engine, get_embedding_provider
from app.db.database import get_session_factory
from app.db.models import WorkflowTask, WorkflowTaskStatus
from app.services.solution_pipeline import run_solution_pipeline

logger = logging.getLogger(__name__)
# External provider calls are bounded at 120 seconds. Keep the lease valid for
# the whole call so a second worker cannot reclaim the same task mid-render.
LEASE_SECONDS = 180
RETRYABLE_CODES = {
    "MODEL_TEMPORARILY_UNAVAILABLE",
    "VERIFIER_UNAVAILABLE",
    "INTERNAL_ERROR",
    "PRESENTATION_RENDER_FAILED",
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
            )
        )
        if (
            task is None
            or task.status != WorkflowTaskStatus.FAILED
            or task.error_code not in RETRYABLE_CODES
            or task.attempt_count >= task.max_attempts
            or (task.deadline_at and task.deadline_at <= datetime.now(UTC))
        ):
            return
        task.status = WorkflowTaskStatus.QUEUED
        task.stage = "queued_retry"
        task.available_at = datetime.now(UTC) + timedelta(seconds=2**task.attempt_count)
        task.finished_at = None
        await session.commit()


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
