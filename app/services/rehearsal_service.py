from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rehearsal import RehearsalAIWorkflow, evaluate_answer_rules
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    CustomerProfile,
    IntelligenceSnapshot,
    RehearsalReport,
    RehearsalSession,
    RehearsalStatus,
    RehearsalTurn,
    ResearchTask,
    ResponseMatrix,
    Session,
    SolutionRun,
)


def evaluate_answer(answer: str, context: dict) -> dict:
    return evaluate_answer_rules(answer, context)


class RehearsalService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        ai_workflow: RehearsalAIWorkflow | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.ai_workflow = ai_workflow or RehearsalAIWorkflow()

    def _filters(self, model) -> tuple:
        return model.workspace_id == self.workspace_id, model.is_deleted.is_(False)

    async def _get(self, model, entity_id: uuid.UUID):
        entity = await self.session.scalar(
            select(model).where(model.id == entity_id, *self._filters(model))
        )
        if entity is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        return entity

    async def create(
        self,
        *,
        customer_profile_id: uuid.UUID,
        title: str | None,
        solution_run_id: uuid.UUID | None,
        research_task_id: uuid.UUID | None,
        intelligence_snapshot_id: uuid.UUID | None,
        response_matrix_id: uuid.UUID | None,
        role: str,
        difficulty: str,
        focus_areas: list[str],
        max_turns: int,
    ) -> RehearsalSession:
        profile = await self._get(CustomerProfile, customer_profile_id)
        solution = await self._get(SolutionRun, solution_run_id) if solution_run_id else None
        research = await self._get(ResearchTask, research_task_id) if research_task_id else None
        intelligence = (
            await self._get(IntelligenceSnapshot, intelligence_snapshot_id)
            if intelligence_snapshot_id
            else None
        )
        matrix = await self._get(ResponseMatrix, response_matrix_id) if response_matrix_id else None
        if solution:
            solution_session_profile = await self.session.scalar(
                select(Session.customer_profile_id).where(Session.id == solution.session_id)
            )
            if solution_session_profile and solution_session_profile != profile.id:
                raise AppError(ErrorCode.VALIDATION_FAILED, "方案与客户画像不匹配", status_code=409)
        if research and research.customer_profile_id != profile.id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "研究任务与客户画像不匹配", status_code=409)
        knowledge_gaps = research.knowledge_gaps if research else []
        context = {
            "schema_version": "v2.0",
            "captured_at": datetime.now(UTC).isoformat(),
            "customer_profile": {
                "id": str(profile.id),
                "name": profile.customer_name,
                "profile": profile.profile,
            },
            "solution": {
                "id": str(solution.id),
                "result": solution.result,
                "retrieval_snapshot": solution.retrieval_snapshot,
            }
            if solution
            else None,
            "research": {
                "id": str(research.id),
                "report": research.report,
                "evidence_snapshot": research.evidence_snapshot,
            }
            if research
            else None,
            "intelligence_snapshot": intelligence.snapshot_data if intelligence else None,
            "response_matrix": matrix.evidence_snapshot if matrix else None,
            "knowledge_gaps": knowledge_gaps,
            "boundary": "external intelligence is context, not enterprise capability evidence",
        }
        context["rehearsal_persona"] = await self.ai_workflow.build_persona(
            context,
            role=role,
            difficulty=difficulty,
            focus_areas=focus_areas,
        )
        rehearsal = RehearsalSession(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            customer_profile_id=profile.id,
            solution_run_id=solution_run_id,
            research_task_id=research_task_id,
            intelligence_snapshot_id=intelligence_snapshot_id,
            response_matrix_id=response_matrix_id,
            title=title or f"{profile.customer_name}方案演练",
            role=role,
            difficulty=difficulty,
            focus_areas=focus_areas,
            max_turns=max_turns,
            status=RehearsalStatus.READY,
            trace_id=str(uuid.uuid4()),
            context_snapshot=context,
            current_turn=0,
        )
        self.session.add(rehearsal)
        await self.session.commit()
        await self.session.refresh(rehearsal)
        return rehearsal

    async def list(self, page: int, page_size: int) -> tuple[list[RehearsalSession], int]:
        filters = self._filters(RehearsalSession)
        rows = await self.session.scalars(
            select(RehearsalSession)
            .where(*filters)
            .order_by(RehearsalSession.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(RehearsalSession).where(*filters)
        )
        return list(rows), int(total or 0)

    async def detail(self, rehearsal_id: uuid.UUID) -> tuple[RehearsalSession, list[RehearsalTurn]]:
        rehearsal = await self._get(RehearsalSession, rehearsal_id)
        turns = list(
            await self.session.scalars(
                select(RehearsalTurn)
                .where(RehearsalTurn.rehearsal_id == rehearsal_id, *self._filters(RehearsalTurn))
                .order_by(RehearsalTurn.sequence)
            )
        )
        return rehearsal, turns

    async def start(self, rehearsal_id: uuid.UUID) -> RehearsalSession:
        rehearsal = await self._get(RehearsalSession, rehearsal_id)
        if rehearsal.status != RehearsalStatus.READY:
            raise AppError(
                ErrorCode.REHEARSAL_STATE_CONFLICT, "当前状态不能开始演练", status_code=409
            )
        rehearsal.status = RehearsalStatus.RUNNING
        rehearsal.started_at = datetime.now(UTC)
        rehearsal.current_turn = 1
        first_question, _ = await self.ai_workflow.generate_question(
            rehearsal.context_snapshot,
            persona=rehearsal.context_snapshot["rehearsal_persona"],
            role=rehearsal.role,
            difficulty=rehearsal.difficulty,
            focus_areas=rehearsal.focus_areas,
            sequence=1,
            history=[],
        )
        first = RehearsalTurn(
            workspace_id=self.workspace_id,
            rehearsal_id=rehearsal.id,
            sequence=1,
            customer_question=first_question,
        )
        self.session.add(first)
        await self.session.commit()
        await self.session.refresh(rehearsal)
        return rehearsal

    async def submit_turn(
        self, rehearsal_id: uuid.UUID, answer: str
    ) -> tuple[RehearsalTurn, RehearsalTurn | None]:
        rehearsal = await self._get(RehearsalSession, rehearsal_id)
        if rehearsal.status != RehearsalStatus.RUNNING:
            raise AppError(ErrorCode.REHEARSAL_STATE_CONFLICT, "演练未在进行中", status_code=409)
        turn = await self.session.scalar(
            select(RehearsalTurn).where(
                RehearsalTurn.rehearsal_id == rehearsal.id,
                RehearsalTurn.sequence == rehearsal.current_turn,
                *self._filters(RehearsalTurn),
            )
        )
        if turn is None or turn.employee_answer is not None:
            raise AppError(
                ErrorCode.REHEARSAL_STATE_CONFLICT, "当前轮次不存在或已回答", status_code=409
            )
        history_rows = list(
            await self.session.scalars(
                select(RehearsalTurn)
                .where(
                    RehearsalTurn.rehearsal_id == rehearsal.id,
                    *self._filters(RehearsalTurn),
                )
                .order_by(RehearsalTurn.sequence)
            )
        )
        turn.employee_answer = answer
        turn.evaluation = await self.ai_workflow.evaluate_answer(
            rehearsal.context_snapshot,
            persona=rehearsal.context_snapshot["rehearsal_persona"],
            question=turn.customer_question,
            answer=answer,
            sequence=turn.sequence,
        )
        next_turn = None
        if rehearsal.current_turn < rehearsal.max_turns:
            rehearsal.current_turn += 1
            history = [
                {
                    "sequence": item.sequence,
                    "customer_question": item.customer_question,
                    "employee_answer": answer if item.id == turn.id else item.employee_answer,
                    "evaluation": turn.evaluation if item.id == turn.id else item.evaluation,
                }
                for item in history_rows
            ]
            next_question, _ = await self.ai_workflow.generate_question(
                rehearsal.context_snapshot,
                persona=rehearsal.context_snapshot["rehearsal_persona"],
                role=rehearsal.role,
                difficulty=rehearsal.difficulty,
                focus_areas=rehearsal.focus_areas,
                sequence=rehearsal.current_turn,
                history=history,
            )
            next_turn = RehearsalTurn(
                workspace_id=self.workspace_id,
                rehearsal_id=rehearsal.id,
                sequence=rehearsal.current_turn,
                customer_question=next_question,
            )
            self.session.add(next_turn)
        await self.session.commit()
        await self.session.refresh(turn)
        if next_turn:
            await self.session.refresh(next_turn)
        return turn, next_turn

    async def complete(self, rehearsal_id: uuid.UUID) -> RehearsalReport:
        rehearsal, turns = await self.detail(rehearsal_id)
        if rehearsal.status != RehearsalStatus.RUNNING:
            raise AppError(
                ErrorCode.REHEARSAL_STATE_CONFLICT, "当前状态不能结束演练", status_code=409
            )
        answered = [turn for turn in turns if turn.evaluation]
        if not answered:
            raise AppError(ErrorCode.REHEARSAL_STATE_CONFLICT, "至少完成一轮回答", status_code=409)
        report_data = await self.ai_workflow.generate_report(
            rehearsal.context_snapshot,
            persona=rehearsal.context_snapshot["rehearsal_persona"],
            turns=[
                {
                    "sequence": turn.sequence,
                    "customer_question": turn.customer_question,
                    "employee_answer": turn.employee_answer,
                    "evaluation": turn.evaluation,
                }
                for turn in answered
            ],
        )
        report = RehearsalReport(
            workspace_id=self.workspace_id,
            rehearsal_id=rehearsal.id,
            score=report_data["average_score"],
            report_data=report_data,
            schema_version="v2.1",
        )
        self.session.add(report)
        rehearsal.status = RehearsalStatus.COMPLETED
        rehearsal.completed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(report)
        return report

    async def get_report(self, rehearsal_id: uuid.UUID) -> RehearsalReport:
        await self._get(RehearsalSession, rehearsal_id)
        report = await self.session.scalar(
            select(RehearsalReport).where(
                RehearsalReport.rehearsal_id == rehearsal_id, *self._filters(RehearsalReport)
            )
        )
        if report is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "演练报告尚未生成", status_code=404)
        return report
