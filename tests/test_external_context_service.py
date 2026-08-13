import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.services.external_context_service import ExternalContextService


@pytest.mark.asyncio
async def test_freeze_rejects_snapshot_for_another_profile() -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        snapshot_data={"items": [{"metadata_snapshot": {"profile_id": str(uuid.uuid4())}}]},
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=snapshot))

    with pytest.raises(AppError) as exc:
        await ExternalContextService(db, workspace_id).freeze(
            profile_id=profile_id,
            intelligence_snapshot_id=snapshot.id,
            response_matrix_id=None,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_freeze_keeps_external_context_separate_from_internal_evidence() -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        snapshot_data={"items": [{"metadata_snapshot": {"profile_id": str(profile_id)}}]},
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=snapshot))

    frozen = await ExternalContextService(db, workspace_id).freeze(
        profile_id=profile_id,
        intelligence_snapshot_id=snapshot.id,
        response_matrix_id=None,
    )

    assert frozen["intelligence_snapshot"]["id"] == str(snapshot.id)
    assert "capabilities" not in frozen
    assert "cannot prove enterprise capability" in frozen["boundary"]
