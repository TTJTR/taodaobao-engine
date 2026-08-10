from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

ROLE_LABELS = {
    "customer_decision_maker": "客户决策人",
    "technical_reviewer": "技术评审",
    "procurement": "采购负责人",
    "challenger": "强势质疑者",
}


def evaluate_answer(answer: str, context: dict) -> dict:
    absolute = re.findall(r"一定|保证|百分之百|绝对|零风险|完全没有|永久|全部支持", answer)
    cited = bool(re.search(r"根据|来源|证据|案例|能力库|经验库|待确认|暂不承诺", answer))
    pending = bool(re.search(r"待确认|需要确认|暂不承诺|尚无依据|需要补充", answer))
    score = 80
    issues: list[dict[str, str]] = []
    if absolute:
        score -= min(40, 10 * len(set(absolute)))
        issues.append({"type": "overcommitment", "detail": "使用了未经边界限定的绝对承诺"})
    if not cited:
        score -= 15
        issues.append({"type": "evidence_boundary", "detail": "没有说明企业依据或信息来源"})
    if not pending and context.get("knowledge_gaps"):
        score -= 10
        issues.append({"type": "pending_confirmation", "detail": "未暴露上下文中的知识缺口"})
    return {
        "score": max(0, score),
        "issues": issues,
        "strengths": (["主动说明了证据或可信边界"] if cited else []),
        "recommended_answer_pattern": (
            "先回应问题，再区分企业依据、外部情报与待确认项，最后给出下一步。"
        ),
    }


class RehearsalService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

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
        first = RehearsalTurn(
            workspace_id=self.workspace_id,
            rehearsal_id=rehearsal.id,
            sequence=1,
            customer_question=self._question(rehearsal, 1),
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
        turn.employee_answer = answer
        turn.evaluation = evaluate_answer(answer, rehearsal.context_snapshot)
        next_turn = None
        if rehearsal.current_turn < rehearsal.max_turns:
            rehearsal.current_turn += 1
            next_turn = RehearsalTurn(
                workspace_id=self.workspace_id,
                rehearsal_id=rehearsal.id,
                sequence=rehearsal.current_turn,
                customer_question=self._question(rehearsal, rehearsal.current_turn),
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
        scores = [int(turn.evaluation["score"]) for turn in answered]
        issues = [issue for turn in answered for issue in turn.evaluation.get("issues", [])]
        report_data = {
            "turn_count": len(answered),
            "average_score": round(sum(scores) / len(scores)),
            "issues": issues,
            "strengths": list(
                dict.fromkeys(
                    strength
                    for turn in answered
                    for strength in turn.evaluation.get("strengths", [])
                )
            ),
            "next_actions": [
                "针对高频异议补充企业证据",
                "把无法证明的能力表述改为待确认事项",
                "根据报告重新演练薄弱问题",
            ],
        }
        report = RehearsalReport(
            workspace_id=self.workspace_id,
            rehearsal_id=rehearsal.id,
            score=report_data["average_score"],
            report_data=report_data,
            schema_version="v2.0",
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

    @staticmethod
    def _question(rehearsal: RehearsalSession, sequence: int) -> str:
        role = ROLE_LABELS.get(rehearsal.role, "客户")
        focus = (
            rehearsal.focus_areas[(sequence - 1) % len(rehearsal.focus_areas)]
            if rehearsal.focus_areas
            else "方案依据"
        )
        prompts = [
            f"作为{role}，我想先确认：你们关于“{focus}”的结论有什么企业依据？",
            f"如果“{focus}”没有现成案例，你为什么认为方案仍然可行？",
            f"请明确“{focus}”中哪些是历史事实、企业能力、AI推断和待确认项。",
            f"对于“{focus}”的风险和失败条件，你准备如何向客户说明？",
            f"如果我要求你现在承诺“{focus}”一定实现，你会怎么回应？",
        ]
        return prompts[(sequence - 1) % len(prompts)]
