import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    ProcessStatus,
    ProfileStatus,
    ResearchTask,
    ResearchTaskStatus,
)
from app.db.repositories import (
    CustomerProfileRepository,
    ExpertReplyRepository,
    MessageRepository,
    ResearchStepRepository,
    ResearchTaskRepository,
    SessionRepository,
)
from app.services.external_context_service import ExternalContextService


class ResearchTaskService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.tasks = ResearchTaskRepository(session, workspace_id)
        self.steps = ResearchStepRepository(session, workspace_id)
        self.profiles = CustomerProfileRepository(session, workspace_id)
        self.sessions = SessionRepository(session, workspace_id)
        self.messages = MessageRepository(session, workspace_id)
        self.replies = ExpertReplyRepository(session, workspace_id)

    async def create(
        self,
        *,
        customer_profile_id: uuid.UUID,
        session_id: uuid.UUID | None,
        title: str | None,
        question: str,
        completion_conditions: list[str],
        intelligence_snapshot_id: uuid.UUID | None = None,
        response_matrix_id: uuid.UUID | None = None,
    ) -> ResearchTask:
        profile = await self.profiles.get(customer_profile_id)
        if profile is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "客户画像不存在", status_code=404)
        if profile.status != ProfileStatus.CONFIRMED:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "Deep Research只能使用已确认客户画像",
                status_code=409,
            )
        conversation: list[dict] = []
        if session_id is not None:
            chat = await self.sessions.get(session_id)
            if chat is None or chat.customer_profile_id != profile.id:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    "研究会话不存在或不属于当前客户",
                    status_code=404,
                )
            conversation = [
                {"role": item.role.value, "content": item.content}
                for item in await self.messages.list_for_session(session_id)
            ]
        normalized_question = question.strip()
        external_context = await ExternalContextService(self.session, self.workspace_id).freeze(
            profile_id=profile.id,
            intelligence_snapshot_id=intelligence_snapshot_id,
            response_matrix_id=response_matrix_id,
        )
        task = await self.tasks.create(
            customer_profile_id=profile.id,
            session_id=session_id,
            created_by_id=self.user_id,
            intelligence_snapshot_id=intelligence_snapshot_id,
            title=(title or normalized_question[:80]).strip(),
            question=normalized_question,
            completion_conditions=list(dict.fromkeys(completion_conditions)),
            status=ResearchTaskStatus.QUEUED,
            stage="queued",
            progress=0,
            trace_id=str(uuid.uuid4()),
            profile_snapshot={
                "id": str(profile.id),
                "customer_name": profile.customer_name,
                "profile": profile.profile,
                "status": profile.status.value,
                "source_ids": [str(item) for item in profile.source_ids],
            },
            conversation_snapshot=conversation,
            findings=[],
            routes=[],
            knowledge_gaps=[],
            expert_questions=[],
            retry_count=0,
            evidence_snapshot={"external_context": external_context} if external_context else None,
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def get(self, task_id: uuid.UUID) -> ResearchTask:
        task = await self.tasks.get(task_id)
        if task is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "研究任务不存在", status_code=404)
        return task

    async def detail(self, task_id: uuid.UUID) -> tuple[ResearchTask, list]:
        task = await self.get(task_id)
        return task, await self.steps.list_for_task(task.id)

    async def list_tasks(
        self,
        *,
        page: int,
        page_size: int,
        status: ResearchTaskStatus | None,
        profile_id: uuid.UUID | None,
    ) -> tuple[list[ResearchTask], int]:
        status_value = status.value if status else None
        items = await self.tasks.list_filtered(
            offset=(page - 1) * page_size,
            limit=page_size,
            status=status_value,
            profile_id=profile_id,
        )
        total = await self.tasks.count_filtered(status=status_value, profile_id=profile_id)
        return items, total

    async def context(self, task_id: uuid.UUID) -> dict:
        task = await self.get(task_id)
        return {
            "research_task_id": task.id,
            "conversation": task.conversation_snapshot,
            "research_document": task.report,
            "research_document_url": task.research_document_url,
            "customer_profile": task.profile_snapshot,
            "evidence_snapshot": task.evidence_snapshot
            or {"experiences": [], "capabilities": [], "created_at": task.created_at.isoformat()},
            "knowledge_gaps": task.knowledge_gaps,
            "pending_questions": task.expert_questions,
        }

    async def cancel(self, task_id: uuid.UUID, reason: str | None) -> ResearchTask:
        await self._lock_task(task_id)
        task = await self.get(task_id)
        if task.status == ResearchTaskStatus.COMPLETED:
            raise AppError(ErrorCode.VALIDATION_FAILED, "已完成研究不能取消", status_code=409)
        task.status = ResearchTaskStatus.CANCELLED
        task.stage = "cancelled"
        task.cancel_reason = reason
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def retry(self, task_id: uuid.UUID) -> ResearchTask:
        await self._lock_task(task_id)
        task = await self.get(task_id)
        if task.status != ResearchTaskStatus.FAILED:
            raise AppError(
                ErrorCode.VALIDATION_FAILED, "只有失败的研究任务可以重试", status_code=409
            )
        task.status = ResearchTaskStatus.QUEUED
        task.error_code = None
        task.error_summary = None
        task.retry_count += 1
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def expert_answers(self, task_id: uuid.UUID) -> list[dict]:
        return [
            {
                "research_task_id": str(item.research_task_id),
                "question_id": item.question_id,
                "author_id": item.author_id,
                "author_name": item.author_name,
                "message_url": item.message_url,
                "answer_text": item.answer_text,
                "source_ids": [],
                "boundary": "pending_confirmation",
            }
            for item in await self.replies.list_for_task(task_id)
        ]

    async def _lock_task(self, task_id: uuid.UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.workspace_id}:research-task:{task_id}"},
        )


def research_task_is_terminal(task: ResearchTask) -> bool:
    return task.status in {
        ResearchTaskStatus.COMPLETED,
        ResearchTaskStatus.CANCELLED,
        ResearchTaskStatus.FAILED,
    }


def mark_step_failed(step, task: ResearchTask, exc: Exception) -> None:
    now = datetime.now(UTC)
    step.status = ProcessStatus.FAILED
    step.error_code = ErrorCode.MODEL_TEMPORARILY_UNAVAILABLE.value
    step.error_summary = str(exc)[:1000]
    step.finished_at = now
    task.status = ResearchTaskStatus.FAILED
    task.error_code = step.error_code
    task.error_summary = step.error_summary
