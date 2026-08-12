import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.db.models import WorkflowTaskStatus
from app.services import durable_worker
from app.services.runtime_service import RuntimeService
from app.services.tender_task_worker import resolve_tender_source


def test_resolve_tender_source_accepts_only_files_below_trusted_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    source = root / "sample.pdf"
    source.write_bytes(b"%PDF-1.7")

    assert resolve_tender_source(source.as_uri(), root) == source.resolve()
    with pytest.raises(AppError, match="受信任存储目录"):
        resolve_tender_source((tmp_path / "outside.pdf").resolve().as_uri(), root)
    with pytest.raises(AppError, match="不受信任"):
        resolve_tender_source("https://example.com/tender.pdf", root)


@pytest.mark.asyncio
async def test_durable_worker_dispatches_tender_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def run(task_id, workspace_id, target_id):
        called.append((task_id, workspace_id, target_id))

    async def reschedule(*_):
        pass

    monkeypatch.setattr("app.services.tender_task_worker.run_tender_parse_task", run)
    monkeypatch.setattr(durable_worker, "_reschedule_retryable_failure", reschedule)
    ids = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await durable_worker.process_claimed_task(ids[0], ids[1], "tender_parse", ids[2])

    assert called == [ids]
    assert durable_worker.LEASE_SECONDS > 300
    assert "TENDER_PARSE_TIMEOUT" in durable_worker.RETRYABLE_CODES


def test_runtime_serializes_tender_parse_task() -> None:
    now = datetime.now(UTC)
    task = SimpleNamespace(
        id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        status=WorkflowTaskStatus.QUEUED,
        stage="queued",
        trace_id="trace-tender",
        payload={"raw_artifact_id": str(uuid.uuid4())},
        error_code=None,
        error_summary=None,
        created_at=now,
        updated_at=now,
    )

    row = RuntimeService(None, uuid.uuid4())._tender_parse(task)

    assert row["id"] == str(task.id)
    assert row["task_type"] == "tender_parse"
    assert row["output_summary"]["tender_id"] == str(task.target_id)
