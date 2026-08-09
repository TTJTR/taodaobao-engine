import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    Message,
    MessageRole,
    ProcessStatus,
    ProfileStatus,
    Session,
    SolutionRun,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.db.repositories import (
    CustomerProfileRepository,
    MessageRepository,
    SessionRepository,
    SolutionRunRepository,
)


class SessionService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.sessions = SessionRepository(session, workspace_id)
        self.messages = MessageRepository(session, workspace_id)
        self.runs = SolutionRunRepository(session, workspace_id)
        self.profiles = CustomerProfileRepository(session, workspace_id)

    async def create(self, customer_profile_id: uuid.UUID, title: str | None) -> Session:
        profile = await self.profiles.get(customer_profile_id)
        if profile is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "客户画像不存在", status_code=404)
        if profile.status != ProfileStatus.CONFIRMED:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "快速方案只能使用已确认客户画像",
                status_code=409,
            )
        chat = await self.sessions.create(
            customer_profile_id=customer_profile_id,
            created_by_id=self.user_id,
            title=(title or "新方案会话").strip() or "新方案会话",
        )
        await self.session.commit()
        await self.session.refresh(chat)
        return chat

    async def get(self, session_id: uuid.UUID) -> Session:
        chat = await self.sessions.get(session_id)
        if chat is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "会话不存在", status_code=404)
        return chat

    async def get_detail(self, session_id: uuid.UUID) -> tuple[Session, list[Message]]:
        chat = await self.get(session_id)
        return chat, await self.messages.list_for_session(session_id)

    async def list_sessions(
        self,
        *,
        page: int,
        page_size: int,
        profile_id: uuid.UUID | None,
    ) -> tuple[list[Session], int]:
        items = await self.sessions.list_filtered(
            offset=(page - 1) * page_size, limit=page_size, profile_id=profile_id
        )
        return items, await self.sessions.count_filtered(profile_id=profile_id)

    async def create_turn(self, session_id: uuid.UUID, content: str) -> tuple[Message, SolutionRun]:
        chat = await self.get(session_id)
        profile = await self.profiles.get(chat.customer_profile_id)
        if profile is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "客户画像不存在", status_code=404)
        if profile.status != ProfileStatus.CONFIRMED:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "快速方案只能使用已确认客户画像",
                status_code=409,
            )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.workspace_id}:session-sequence:{session_id}"},
        )
        message = await self.messages.create(
            session_id=session_id,
            solution_run_id=None,
            role=MessageRole.USER,
            content=content.strip(),
            sequence=await self.messages.next_sequence(session_id),
        )
        run = await self.runs.create(
            session_id=session_id,
            request_message_id=message.id,
            profile_snapshot={
                "id": str(profile.id),
                "customer_name": profile.customer_name,
                "profile": profile.profile,
                "status": profile.status.value,
            },
            status=ProcessStatus.PENDING,
            retryable=False,
            stage="queued",
            deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        self.session.add(
            WorkflowTask(
                workspace_id=self.workspace_id,
                kind="solution_run",
                target_id=run.id,
                status=WorkflowTaskStatus.QUEUED,
                stage="queued",
                trace_id=run.trace_id,
                payload={},
                attempt_count=0,
                max_attempts=3,
                available_at=datetime.now(UTC),
                deadline_at=run.deadline_at,
            )
        )
        chat.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(message)
        await self.session.refresh(run)
        return message, run
