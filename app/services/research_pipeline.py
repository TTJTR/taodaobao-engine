import uuid
from datetime import UTC, datetime

from app.ai.embedding import EmbeddingProvider
from app.contracts.ai import AIEngine
from app.db.database import get_session_factory
from app.db.models import ProcessStatus, ResearchTaskStatus
from app.db.repositories import ResearchStepRepository, ResearchTaskRepository
from app.services.ai_payload_adapter import (
    build_solution_context,
    normalize_retrieval_snapshot,
)
from app.services.ai_run_service import persist_last_ai_run
from app.services.research_service import (
    ResearchTaskService,
    mark_step_failed,
    research_task_is_terminal,
)
from app.services.retrieval_service import RetrievalService

RESEARCH_STAGES = (
    "analysis",
    "planning",
    "evidence_synthesis",
    "route_comparison",
    "fact_audit",
    "final_report",
)
STAGE_PROGRESS = {
    "analysis": 10,
    "planning": 25,
    "evidence_synthesis": 50,
    "route_comparison": 65,
    "fact_audit": 80,
    "awaiting_expert": 85,
    "final_report": 95,
}


async def run_research_pipeline(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    ai_engine: AIEngine,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        tasks = ResearchTaskRepository(session, workspace_id)
        steps = ResearchStepRepository(session, workspace_id)
        task = await tasks.get(task_id)
        if task is None or research_task_is_terminal(task):
            return
        try:
            completed = {
                item.stage
                for item in await steps.list_for_task(task.id)
                if item.status == ProcessStatus.COMPLETED
            }
            if task.evidence_snapshot is None:
                retrieval_context = build_solution_context(task.question, task.profile_snapshot)
                raw_snapshot = await RetrievalService(session, workspace_id).retrieve(
                    task.question,
                    context=retrieval_context,
                    ai_engine=ai_engine,
                    embedding_provider=embedding_provider,
                )
                persist_last_ai_run(
                    session,
                    workspace_id,
                    ai_engine,
                    target_type="research_task",
                    target_id=task.id,
                    input_summary={"stage": "search_intent"},
                )
                task.evidence_snapshot = normalize_retrieval_snapshot(raw_snapshot)
                await session.commit()

            if "fact_audit" in completed and task.status == ResearchTaskStatus.RESEARCHING:
                answers = await ResearchTaskService(session, workspace_id, user_id).expert_answers(
                    task.id
                )
                if answers and "awaiting_expert" not in completed:
                    await _run_stage(session, task, steps, "awaiting_expert", ai_engine, answers)
                    completed.add("awaiting_expert")

            for stage in RESEARCH_STAGES:
                await session.refresh(task)
                if (
                    research_task_is_terminal(task)
                    or task.status == ResearchTaskStatus.WAITING_EXPERT
                ):
                    return
                if stage in completed:
                    continue
                result = await _run_stage(session, task, steps, stage, ai_engine, [])
                completed.add(stage)
                if result.get("status") == "awaiting_expert":
                    task.status = ResearchTaskStatus.WAITING_EXPERT
                    task.stage = "waiting_expert"
                    task.progress = STAGE_PROGRESS["awaiting_expert"]
                    await session.commit()
                    return
            task.status = ResearchTaskStatus.COMPLETED
            task.stage = "completed"
            task.progress = 100
            task.completed_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            task = await tasks.get(task_id)
            if task is None:
                return
            persist_last_ai_run(
                session,
                workspace_id,
                ai_engine,
                target_type="research_task",
                target_id=task.id,
                input_summary={"stage": task.stage, "failed": True},
            )
            active_steps = await steps.list_for_task(task.id)
            active = next(
                (item for item in reversed(active_steps) if item.status == ProcessStatus.RUNNING),
                None,
            )
            if active is not None:
                mark_step_failed(active, task, exc)
            else:
                task.status = ResearchTaskStatus.FAILED
                task.error_code = "MODEL_TEMPORARILY_UNAVAILABLE"
                task.error_summary = str(exc)[:1000]
            await session.commit()


async def _run_stage(session, task, steps, stage, ai_engine, expert_answers) -> dict:
    existing = await steps.list_for_task(task.id)
    step = await steps.create(
        research_task_id=task.id,
        sequence=len(existing) + 1,
        stage=stage,
        status=ProcessStatus.RUNNING,
        attempt=task.retry_count + 1,
        input_snapshot={
            "research_plan": task.research_plan,
            "findings_count": len(task.findings),
            "expert_answer_count": len(expert_answers),
        },
        started_at=datetime.now(UTC),
    )
    task.status = (
        ResearchTaskStatus.PLANNING
        if stage in {"analysis", "planning"}
        else ResearchTaskStatus.SYNTHESIZING
        if stage == "final_report"
        else ResearchTaskStatus.RESEARCHING
    )
    task.stage = stage
    task.progress = STAGE_PROGRESS[stage]
    await session.commit()

    context = build_solution_context(task.question, task.profile_snapshot)
    context.update(
        {
            "mode": "deep",
            "stage": stage,
            "trace_id": task.trace_id,
            "research_task_id": str(task.id),
            "research_plan": task.research_plan,
            "prior_findings": task.findings,
            "expert_answers": expert_answers,
            "conversation_history": task.conversation_snapshot[-20:],
        }
    )
    result = await ai_engine.generate_solution(context, task.evidence_snapshot)
    persist_last_ai_run(
        session,
        task.workspace_id,
        ai_engine,
        target_type="research_task",
        target_id=task.id,
        input_summary={"stage": stage, "expert_answer_count": len(expert_answers)},
    )
    if result.get("research_task_id") != str(task.id) or result.get("stage") != stage:
        raise ValueError("AI research result identity does not match the running task")

    step.status = ProcessStatus.COMPLETED
    step.output_snapshot = result
    step.finished_at = datetime.now(UTC)
    task.research_plan = result.get("research_plan") or task.research_plan
    task.findings = result.get("findings") or task.findings
    task.routes = result.get("routes") or task.routes
    task.audit = result.get("audit") or task.audit
    expert_context = result.get("expert_context") or {}
    task.knowledge_gaps = expert_context.get("knowledge_gaps") or task.knowledge_gaps
    task.expert_questions = expert_context.get("questions") or task.expert_questions
    task.report = result.get("final_solution") or task.report
    await session.commit()
    return result
