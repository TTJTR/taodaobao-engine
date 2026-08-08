import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import ProcessStatus
from app.integrations.ai_engine import MockAIEngine
from app.services import solution_pipeline as module


@pytest.mark.asyncio
async def test_solution_pipeline_completes_and_appends_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        request_message_id=uuid.uuid4(),
        status=ProcessStatus.PENDING,
        retrieval_snapshot=None,
        result=None,
        display_text=None,
        completed_at=None,
        error_code=None,
        retryable=False,
        profile_snapshot={"customer_name": "制造客户"},
    )
    request_message = SimpleNamespace(id=run.request_message_id, content="视觉质检")
    run_repository = SimpleNamespace(get=AsyncMock(return_value=run))
    message_repository = SimpleNamespace(
        get=AsyncMock(return_value=request_message),
        next_sequence=AsyncMock(return_value=2),
        create=AsyncMock(),
    )
    db = AsyncMock()

    @asynccontextmanager
    async def session_context():
        yield db

    snapshot = {"experiences": [], "capabilities": [], "created_at": "now"}
    monkeypatch.setattr(module, "get_session_factory", lambda: session_context)
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: run_repository)
    monkeypatch.setattr(module, "MessageRepository", lambda *_: message_repository)
    monkeypatch.setattr(
        module,
        "RetrievalService",
        lambda *_: SimpleNamespace(retrieve=AsyncMock(return_value=snapshot)),
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    await module.run_solution_pipeline(run.id, workspace_id, MockAIEngine())

    assert run.status == ProcessStatus.COMPLETED
    assert run.retrieval_snapshot == {
        "experiences": [],
        "capabilities": [],
        "conflicts": [],
        "missing_information": [],
        "gap_summary": None,
        "can_generate_solution": True,
        "created_at": "now",
    }
    assert set(run.result) == {
        "requirement_understanding",
        "initial_recommendations",
        "historical_evidence",
        "capability_composition",
        "prerequisites_and_risks",
        "pending_confirmations",
        "sources",
        "suggested_questions",
    }
    message_repository.create.assert_awaited_once()
    assert message_repository.create.await_args.kwargs["role"].value == "assistant"
