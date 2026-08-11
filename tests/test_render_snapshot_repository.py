import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.repositories.entities import PresentationRenderSnapshotRepository


def _values() -> dict:
    return {
        "presentation_id": uuid.uuid4(),
        "version": 1,
        "schema_version": "presentation-render-snapshot-v1",
        "fact_ledger_hash": "a" * 64,
        "positioned_spec_hash": "b" * 64,
        "compiled_style_hash": "c" * 64,
        "render_ir_hash": "d" * 64,
        "fact_ledger_json": {"schema_version": "fact-ledger-v1"},
        "render_ir_json": {"schema_version": "render-ir-v1"},
        "renderer_versions": {"render_ir_builder": "v1"},
        "diagnostics": {"mode": "shadow"},
    }


@pytest.mark.asyncio
async def test_render_snapshot_retry_reuses_identical_immutable_version() -> None:
    values = _values()
    existing = SimpleNamespace(**values)
    session = AsyncMock()
    session.scalar.return_value = existing
    repository = PresentationRenderSnapshotRepository(session, uuid.uuid4())

    result = await repository.create_or_verify(**values)

    assert result is existing
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_render_snapshot_rejects_different_content_for_same_version() -> None:
    values = _values()
    existing = SimpleNamespace(**values)
    session = AsyncMock()
    session.scalar.return_value = existing
    repository = PresentationRenderSnapshotRepository(session, uuid.uuid4())
    changed = {**values, "render_ir_hash": "e" * 64}

    with pytest.raises(ValueError, match="different content"):
        await repository.create_or_verify(**changed)


@pytest.mark.asyncio
async def test_render_snapshot_forbids_mutation_and_deletion() -> None:
    repository = PresentationRenderSnapshotRepository(AsyncMock(), uuid.uuid4())

    with pytest.raises(TypeError, match="immutable"):
        await repository.update(SimpleNamespace(), render_ir_hash="f" * 64)
    with pytest.raises(TypeError, match="cannot be soft deleted"):
        await repository.soft_delete(SimpleNamespace())
