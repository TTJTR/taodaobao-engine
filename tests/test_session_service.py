import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import Message, ProcessStatus, ProfileStatus, Session, SolutionRun
from app.services import session_service as module


class EntityRepository:
    def __init__(self, entity_type, workspace_id: uuid.UUID) -> None:
        self.entity_type = entity_type
        self.workspace_id = workspace_id
        self.entities = {}

    async def create(self, **values):
        entity = self.entity_type(id=uuid.uuid4(), workspace_id=self.workspace_id, **values)
        self.entities[entity.id] = entity
        return entity

    async def get(self, entity_id):
        return self.entities.get(entity_id)


@pytest.mark.asyncio
async def test_create_turn_persists_user_message_and_pending_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    chat = Session(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        customer_profile_id=profile_id,
        created_by_id=uuid.uuid4(),
        title="试点会话",
    )
    session_repository = EntityRepository(Session, workspace_id)
    session_repository.entities[chat.id] = chat
    message_repository = EntityRepository(Message, workspace_id)
    message_repository.next_sequence = AsyncMock(return_value=1)
    run_repository = EntityRepository(SolutionRun, workspace_id)
    profile = SimpleNamespace(
        id=profile_id,
        customer_name="制造客户",
        profile={"industry": "制造"},
        status=ProfileStatus.CONFIRMED,
    )
    monkeypatch.setattr(module, "SessionRepository", lambda *_: session_repository)
    monkeypatch.setattr(module, "MessageRepository", lambda *_: message_repository)
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: run_repository)
    monkeypatch.setattr(
        module,
        "CustomerProfileRepository",
        lambda *_: SimpleNamespace(get=AsyncMock(return_value=profile)),
    )
    db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock(), execute=AsyncMock())

    message, run = await module.SessionService(db, workspace_id, uuid.uuid4()).create_turn(
        chat.id, "需要一个旁路质检试点"
    )

    assert message.role.value == "user"
    assert message.sequence == 1
    assert run.request_message_id == message.id
    assert run.status == ProcessStatus.PENDING
    assert run.stage == "queued"
    db.add.assert_called_once()
    assert run.profile_snapshot["customer_name"] == "制造客户"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_turn_freezes_explicit_external_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    chat = Session(
        id=uuid.uuid4(), workspace_id=workspace_id, customer_profile_id=profile_id,
        created_by_id=uuid.uuid4(), title="test",
    )
    session_repository = EntityRepository(Session, workspace_id)
    session_repository.entities[chat.id] = chat
    message_repository = EntityRepository(Message, workspace_id)
    message_repository.next_sequence = AsyncMock(return_value=1)
    run_repository = EntityRepository(SolutionRun, workspace_id)
    profile = SimpleNamespace(
        id=profile_id, customer_name="customer", profile={}, status=ProfileStatus.CONFIRMED,
    )
    context = {"intelligence_snapshot": {"id": "snapshot"}, "response_matrix": None}
    monkeypatch.setattr(module, "SessionRepository", lambda *_: session_repository)
    monkeypatch.setattr(module, "MessageRepository", lambda *_: message_repository)
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: run_repository)
    monkeypatch.setattr(
        module,
        "CustomerProfileRepository",
        lambda *_: SimpleNamespace(get=AsyncMock(return_value=profile)),
    )
    monkeypatch.setattr(module.ExternalContextService, "freeze", AsyncMock(return_value=context))
    db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock(), execute=AsyncMock())

    _, run = await module.SessionService(db, workspace_id, uuid.uuid4()).create_turn(
        chat.id, "need a plan", intelligence_snapshot_id=uuid.uuid4()
    )

    assert run.retrieval_snapshot == {"external_context": context}
