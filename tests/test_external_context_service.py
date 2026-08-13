import uuid
from copy import deepcopy
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


@pytest.mark.asyncio
async def test_freeze_exposes_conflicts_and_review_gaps_from_immutable_snapshot() -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    conflict_group_id = str(uuid.uuid4())
    snapshot_data = {
        "items": [
            {
                "id": str(uuid.uuid4()),
                "facts": [{"value": "A"}],
                "summary": "candidate A",
                "conflict_group_id": conflict_group_id,
                "review_status": "needs_review",
                "freshness": "current",
                "metadata_snapshot": {"profile_id": str(profile_id), "field_name": "industry"},
            },
            {
                "id": str(uuid.uuid4()),
                "facts": [{"value": "B"}],
                "summary": "candidate B",
                "conflict_group_id": conflict_group_id,
                "review_status": "confirmed",
                "freshness": "current",
                "metadata_snapshot": {"profile_id": str(profile_id), "field_name": "industry"},
            },
        ]
    }
    snapshot = SimpleNamespace(id=uuid.uuid4(), snapshot_data=snapshot_data)
    db = SimpleNamespace(scalar=AsyncMock(return_value=snapshot))

    frozen = await ExternalContextService(db, workspace_id).freeze(
        profile_id=profile_id,
        intelligence_snapshot_id=snapshot.id,
        response_matrix_id=None,
    )
    model_context = frozen["intelligence_snapshot"]["model_context"]

    assert len(model_context["conflicts"]) == 1
    assert len(model_context["conflicts"][0]["candidates"]) == 2
    assert "unconfirmed" in model_context["conflicts"][0]["message"]
    assert len(model_context["needs_review"]) == 1
    assert frozen["intelligence_snapshot"]["snapshot_data"] == snapshot_data


@pytest.mark.asyncio
async def test_freeze_rejects_cross_workspace_snapshot_as_not_found() -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))

    with pytest.raises(AppError) as exc:
        await ExternalContextService(db, uuid.uuid4()).freeze(
            profile_id=uuid.uuid4(),
            intelligence_snapshot_id=uuid.uuid4(),
            response_matrix_id=None,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_frozen_context_is_unchanged_after_source_snapshot_mutation() -> None:
    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        snapshot_data={
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "facts": [{"value": "frozen value"}],
                    "metadata_snapshot": {"profile_id": str(profile_id)},
                }
            ]
        },
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=snapshot))
    frozen = await ExternalContextService(db, workspace_id).freeze(
        profile_id=profile_id,
        intelligence_snapshot_id=snapshot.id,
        response_matrix_id=None,
    )
    persisted_run_context = deepcopy(frozen)

    snapshot.snapshot_data["items"][0]["facts"] = []
    snapshot.snapshot_data["items"][0]["source_deleted"] = True

    assert persisted_run_context["intelligence_snapshot"]["snapshot_data"]["items"][0][
        "facts"
    ] == [{"value": "frozen value"}]
    assert "source_deleted" not in str(persisted_run_context)
