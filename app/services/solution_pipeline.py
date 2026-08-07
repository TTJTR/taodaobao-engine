import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.contracts.ai import AIEngine
from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import MessageRole, ProcessStatus
from app.db.repositories import MessageRepository, SolutionRunRepository
from app.services.ai_harness import AIHarness
from app.services.retrieval_service import RetrievalService


async def run_solution_pipeline(
    run_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ai_engine: AIEngine,
    *,
    delay_seconds: float = 2.0,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        runs = SolutionRunRepository(session, workspace_id)
        messages = MessageRepository(session, workspace_id)
        run = await runs.get(run_id)
        if run is None:
            return
        try:
            run.status = ProcessStatus.RUNNING
            await session.commit()
            await asyncio.sleep(delay_seconds)
            request_message = await messages.get(run.request_message_id)
            if request_message is None:
                raise RuntimeError("request message is missing")

            snapshot = await RetrievalService(session, workspace_id).retrieve(
                request_message.content
            )
            run.retrieval_snapshot = snapshot
            solution = await AIHarness(
                ai_engine, session, workspace_id
            ).run_solution_generation(
                run.id,
                {
                    "requirement": request_message.content,
                    "profile_snapshot": run.profile_snapshot,
                },
                snapshot,
            )
            if solution is None:
                return

            run.result = solution
            run.display_text = "快速方案已生成，请按八区块核对依据与待确认项。"
            run.status = ProcessStatus.COMPLETED
            run.completed_at = datetime.now(UTC)
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
            await session.commit()
        except Exception:
            await session.rollback()
            run = await runs.get(run_id)
            if run is not None:
                run.status = ProcessStatus.FAILED
                run.error_code = ErrorCode.INTERNAL_ERROR.value
                run.retryable = True
                run.completed_at = datetime.now(UTC)
                await session.commit()
