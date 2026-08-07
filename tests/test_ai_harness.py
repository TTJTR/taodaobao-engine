import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import ProcessStatus
from app.services import ai_harness as module


def valid_solution() -> dict:
    return {name: [] for name in module.REQUIRED_SOLUTION_SECTIONS}


class FlakyEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_solution(self, context: dict, snapshot: dict) -> dict:
        self.calls += 1
        if self.calls < 3:
            raise TimeoutError("model timeout")
        return valid_solution()


class BrokenEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_solution(self, context: dict, snapshot: dict) -> dict:
        self.calls += 1
        raise ConnectionError("model gateway unavailable")


@pytest.mark.asyncio
async def test_harness_retries_twice_then_returns_valid_solution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FlakyEngine()
    repository = SimpleNamespace(get=AsyncMock())
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: repository)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    harness = module.AIHarness(
        engine, AsyncMock(), uuid.uuid4(), max_retries=2
    )

    result = await harness.run_solution_generation(uuid.uuid4(), {}, {})

    assert result == valid_solution()
    assert engine.calls == 3
    repository.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_exhaustion_marks_run_failed_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        status=ProcessStatus.RUNNING,
        error_code=None,
        retryable=False,
        completed_at=None,
    )
    repository = SimpleNamespace(get=AsyncMock(return_value=run))
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: repository)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    session = AsyncMock()
    engine = BrokenEngine()
    harness = module.AIHarness(engine, session, uuid.uuid4(), max_retries=2)

    result = await harness.run_solution_generation(uuid.uuid4(), {}, {})

    assert result is None
    assert engine.calls == 3
    assert run.status == ProcessStatus.FAILED
    assert run.error_code == "MODEL_TEMPORARILY_UNAVAILABLE"
    assert run.retryable is True
    assert run.completed_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_harness_rejects_incomplete_eight_section_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        status=ProcessStatus.RUNNING,
        error_code=None,
        retryable=False,
        completed_at=None,
    )
    repository = SimpleNamespace(get=AsyncMock(return_value=run))
    engine = SimpleNamespace(generate_solution=AsyncMock(return_value={"sources": []}))
    monkeypatch.setattr(module, "SolutionRunRepository", lambda *_: repository)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    harness = module.AIHarness(engine, AsyncMock(), uuid.uuid4(), max_retries=1)

    result = await harness.run_solution_generation(uuid.uuid4(), {}, {})

    assert result is None
    assert engine.generate_solution.await_count == 2
    assert run.status == ProcessStatus.FAILED
