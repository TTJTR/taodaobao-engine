import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import ProcessStatus
from app.integrations.ai_engine import MockAIEngine
from app.services import solution_pipeline as module


@pytest.mark.asyncio
async def test_solution_pipeline_rejects_legacy_solution_without_claim_ledger(
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
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        attempt_count=0,
        result_version=1,
        trace_id=str(uuid.uuid4()),
        stage="queued",
    )
    request_message = SimpleNamespace(id=run.request_message_id, content="视觉质检")
    run_repository = SimpleNamespace(get=AsyncMock(return_value=run))
    message_repository = SimpleNamespace(
        get=AsyncMock(return_value=request_message),
        next_sequence=AsyncMock(return_value=2),
        create=AsyncMock(),
        list_for_session=AsyncMock(return_value=[]),
        get_for_solution_run=AsyncMock(return_value=None),
    )
    db = SimpleNamespace(
        add=Mock(),
        scalar=AsyncMock(return_value=None),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

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

    assert run.status == ProcessStatus.FAILED
    assert run.error_code == "AI_OUTPUT_INVALID"
    assert run.retrieval_snapshot == {
        "experiences": [],
        "capabilities": [],
        "conflicts": [],
        "missing_information": [],
        "gap_summary": None,
        "can_generate_solution": True,
        "created_at": "now",
    }
    assert run.result is None
    message_repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieval_snapshot_is_updated_on_same_version_retry() -> None:
    workspace_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), result_version=3)
    existing = SimpleNamespace(
        snapshot_data={"old": True},
        source_versions={},
        permission_snapshot={},
        embedding_version=None,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=existing), add=Mock())
    snapshot = {
        "experiences": [
            {
                "source_id": "src-1",
                "source_version": "v2",
                "permission_status": "granted",
                "permission_checked_at": "2026-08-16T00:00:00Z",
            }
        ],
        "capabilities": [],
    }

    await module._persist_retrieval_snapshot(
        db,
        workspace_id=workspace_id,
        run=run,
        raw_snapshot=snapshot,
    )

    db.add.assert_not_called()
    assert existing.snapshot_data == snapshot
    assert existing.source_versions == {"src-1": "v2"}
    assert existing.permission_snapshot["src-1"]["status"] == "granted"
