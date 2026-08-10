import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    AIRunRecord,
    Presentation,
    RehearsalSession,
    ResearchStep,
    ResearchTask,
    SearchRun,
    SolutionRun,
    TrustDecisionRecord,
    WorkflowTask,
)


def _value(value) -> str:
    return getattr(value, "value", value)


class RuntimeService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def _filters(self, model) -> tuple:
        return model.workspace_id == self.workspace_id, model.is_deleted.is_(False)

    async def list_tasks(
        self, *, page: int, page_size: int, task_type: str | None, status: str | None
    ) -> tuple[list[dict], int]:
        rows: list[dict] = []
        if task_type in {None, "quick_solution"}:
            for item in await self._all(SolutionRun):
                trust = await self.session.scalar(
                    select(TrustDecisionRecord)
                    .where(
                        TrustDecisionRecord.solution_run_id == item.id,
                        *self._filters(TrustDecisionRecord),
                    )
                    .order_by(TrustDecisionRecord.version.desc())
                    .limit(1)
                )
                rows.append(self._solution(item, _value(trust.action) if trust else None))
        if task_type in {None, "deep_research"}:
            rows.extend(self._research(item) for item in await self._all(ResearchTask))
        if task_type in {None, "presentation"}:
            rows.extend(self._presentation(item) for item in await self._all(Presentation))
        if task_type in {None, "intelligence"}:
            rows.extend(self._search(item) for item in await self._all(SearchRun))
        if task_type in {None, "rehearsal"}:
            rows.extend(self._rehearsal(item) for item in await self._all(RehearsalSession))
        if status:
            rows = [row for row in rows if row["status"] == status]
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        total = len(rows)
        return rows[(page - 1) * page_size : page * page_size], total

    async def detail(self, task_id: uuid.UUID) -> dict:
        for model, serializer in (
            (SolutionRun, self._solution),
            (ResearchTask, self._research),
            (Presentation, self._presentation),
            (SearchRun, self._search),
            (RehearsalSession, self._rehearsal),
        ):
            item = await self.session.scalar(
                select(model).where(model.id == task_id, *self._filters(model))
            )
            if item:
                if model is SolutionRun:
                    trust = await self.session.scalar(
                        select(TrustDecisionRecord)
                        .where(
                            TrustDecisionRecord.solution_run_id == item.id,
                            *self._filters(TrustDecisionRecord),
                        )
                        .order_by(TrustDecisionRecord.version.desc())
                        .limit(1)
                    )
                    return serializer(item, _value(trust.action) if trust else None)
                return serializer(item)
        raise AppError(ErrorCode.VALIDATION_FAILED, "运行任务不存在", status_code=404)

    async def steps(self, task_id: uuid.UUID) -> list[dict]:
        task = await self.detail(task_id)
        task_type = task["task_type"]
        if task_type == "deep_research":
            rows = list(
                await self.session.scalars(
                    select(ResearchStep)
                    .where(ResearchStep.research_task_id == task_id, *self._filters(ResearchStep))
                    .order_by(ResearchStep.sequence)
                )
            )
            return [
                {
                    "id": str(row.id),
                    "sequence": row.sequence,
                    "stage": row.stage,
                    "executor": "deep_research_workflow",
                    "is_agent": row.stage
                    in {
                        "experience_retrieval",
                        "capability_retrieval",
                        "solution_orchestration",
                        "fact_audit",
                    },
                    "status": _value(row.status),
                    "attempt": row.attempt,
                    "output_summary": row.output_snapshot or {},
                    "error_code": row.error_code,
                    "error_summary": row.error_summary,
                }
                for row in rows
            ]
        if task_type in {"quick_solution", "presentation"}:
            target_type = "solution_run" if task_type == "quick_solution" else "presentation"
            ai_rows = list(
                await self.session.scalars(
                    select(AIRunRecord)
                    .where(
                        AIRunRecord.target_id == task_id,
                        AIRunRecord.target_type == target_type,
                        *self._filters(AIRunRecord),
                    )
                    .order_by(AIRunRecord.created_at)
                )
            )
            workflow = await self.session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.target_id == task_id, *self._filters(WorkflowTask)
                )
            )
            result = [
                {
                    "id": str(row.id),
                    "sequence": index,
                    "stage": row.stage or row.method,
                    "executor": row.method,
                    "is_agent": False,
                    "status": _value(row.status),
                    "attempt": 1,
                    "output_summary": row.input_summary,
                    "error_code": row.error_code,
                    "error_summary": None,
                }
                for index, row in enumerate(ai_rows, 1)
            ]
            if workflow:
                result.append(
                    {
                        "id": str(workflow.id),
                        "sequence": len(result) + 1,
                        "stage": workflow.stage,
                        "executor": "durable_worker",
                        "is_agent": False,
                        "status": _value(workflow.status),
                        "attempt": workflow.attempt_count,
                        "output_summary": {},
                        "error_code": workflow.error_code,
                        "error_summary": workflow.error_summary,
                    }
                )
            return result
        return [
            {
                "id": str(task_id),
                "sequence": 1,
                "stage": task["stage"],
                "executor": "deterministic_v2_service",
                "is_agent": False,
                "status": task["status"],
                "attempt": 1,
                "output_summary": task["output_summary"],
                "error_code": task["error_code"],
                "error_summary": task["error_summary"],
            }
        ]

    async def _all(self, model) -> list:
        return list(
            await self.session.scalars(
                select(model)
                .where(*self._filters(model))
                .order_by(model.updated_at.desc())
                .limit(500)
            )
        )

    @staticmethod
    def _base(
        item,
        task_type: str,
        title: str,
        status: str,
        stage: str,
        progress: int,
        trace_id: str | None,
        **extra,
    ) -> dict:
        return {
            "id": str(item.id),
            "task_type": task_type,
            "title": title,
            "status": status,
            "stage": stage,
            "progress": progress,
            "trace_id": trace_id,
            "trust_action": extra.get("trust_action"),
            "output_summary": extra.get("output_summary", {}),
            "error_code": extra.get("error_code"),
            "error_summary": extra.get("error_summary"),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    def _solution(self, item: SolutionRun, trust_action: str | None = None) -> dict:
        progress = (
            100
            if _value(item.status) == "completed"
            else 10
            if _value(item.status) == "pending"
            else 60
        )
        return self._base(
            item,
            "quick_solution",
            f"快速方案 {str(item.id)[:8]}",
            _value(item.status),
            item.stage,
            progress,
            item.trace_id,
            trust_action=trust_action,
            output_summary={"result_version": item.result_version, "has_result": bool(item.result)},
            error_code=item.error_code,
        )

    def _research(self, item: ResearchTask) -> dict:
        return self._base(
            item,
            "deep_research",
            item.title,
            _value(item.status),
            item.stage,
            item.progress,
            item.trace_id,
            output_summary={
                "knowledge_gap_count": len(item.knowledge_gaps),
                "has_report": bool(item.report),
            },
            error_code=item.error_code,
            error_summary=item.error_summary,
        )

    def _presentation(self, item: Presentation) -> dict:
        status = _value(item.status)
        progress = 100 if status == "ready" else 0 if status == "draft" else 60
        return self._base(
            item,
            "presentation",
            f"展示稿 {str(item.id)[:8]}",
            status,
            status,
            progress,
            item.trace_id,
            output_summary={"version": item.version, "requested_outputs": item.requested_outputs},
            error_code=item.error_code,
        )

    def _search(self, item: SearchRun) -> dict:
        status = _value(item.status)
        return self._base(
            item,
            "intelligence",
            item.query,
            status,
            "captured" if status in {"completed", "partial"} else status,
            100 if status in {"completed", "partial"} else 40,
            item.trace_id,
            output_summary=item.result_summary,
            error_code=item.error_code,
            error_summary=item.error_summary,
        )

    def _rehearsal(self, item: RehearsalSession) -> dict:
        status = _value(item.status)
        progress = (
            100
            if status == "completed"
            else round(item.current_turn / item.max_turns * 100)
            if status == "running"
            else 0
        )
        return self._base(
            item,
            "rehearsal",
            item.title,
            status,
            "report" if status == "completed" else "conversation",
            progress,
            item.trace_id,
            output_summary={"current_turn": item.current_turn, "max_turns": item.max_turns},
            error_code=item.error_code,
        )
