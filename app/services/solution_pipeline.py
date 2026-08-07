import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.errors import ErrorCode
from app.db.database import get_session_factory
from app.db.models import MessageRole, ProcessStatus
from app.db.repositories import MessageRepository, SolutionRunRepository
from app.services.retrieval_service import RetrievalService


async def run_solution_pipeline(
    run_id: uuid.UUID, workspace_id: uuid.UUID, *, delay_seconds: float = 2.0
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
            solution = _build_mock_solution(request_message.content, snapshot)
            run.retrieval_snapshot = snapshot
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


def _build_mock_solution(requirement: str, snapshot: dict) -> dict:
    experiences = snapshot["experiences"]
    capabilities = snapshot["capabilities"]
    cited_experiences = [
        {
            "text": item["data"].get("solution", item["data"].get("name", "历史经验")),
            "boundary": "historical_fact",
            "asset_id": item["id"],
            "source_id": item["source_id"],
        }
        for item in experiences
    ]
    cited_capabilities = [
        {
            "text": item["data"].get("description", item["data"].get("name", "企业能力")),
            "boundary": "enterprise_capability",
            "asset_id": item["id"],
            "source_id": item["source_id"],
        }
        for item in capabilities
    ]
    return {
        "requirement_understanding": [
            {
                "text": requirement,
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [
            {
                "text": "优先以小范围旁路试点验证价值，再根据验收结果扩展。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "historical_evidence": cited_experiences,
        "capability_composition": cited_capabilities,
        "prerequisites_and_risks": [
            {
                "text": "需确认数据权限、接口条件、样本质量与验收口径。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "pending_confirmations": [
            {
                "text": "试点范围、周期、预算和最终决策人仍需客户确认。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "sources": [
            {"asset_id": item["id"], "source_id": item["source_id"], "type": kind}
            for kind, items in (("experience", experiences), ("capability", capabilities))
            for item in items
        ],
        "suggested_questions": [
            "第一阶段试点的业务范围是什么？",
            "现有系统可以开放哪些数据与接口？",
            "客户将用哪些指标验收？",
        ],
    }
