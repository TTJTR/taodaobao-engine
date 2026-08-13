import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import AIEngineDependency, CurrentUser, DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.models import IntelligenceFreshness
from app.schemas.intelligence_provider import EnrichmentJobRequest
from app.schemas.v2 import (
    CreateIntelligenceSnapshotRequest,
    CreateProfileProposalRequest,
    CreateSearchRunRequest,
    DecideProposalRequest,
    EnrichRawArtifactRequest,
    QueueProviderEnrichmentRequest,
    ReassessIntelligenceFreshnessRequest,
)
from app.services.intelligence_service import IntelligenceService

router = APIRouter(route_class=IdempotencyRoute)
profile_router = APIRouter(route_class=IdempotencyRoute)


def _run(row) -> dict:
    return {
        "id": str(row.id),
        "query": row.query,
        "purpose": row.purpose,
        "provider": row.provider,
        "status": row.status.value,
        "trace_id": row.trace_id,
        "input_snapshot": row.input_snapshot,
        "result_summary": row.result_summary,
        "error_code": row.error_code,
        "error_summary": row.error_summary,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _snapshot(row) -> dict:
    return {
        "id": str(row.id),
        "purpose": row.purpose,
        "item_ids": row.item_ids,
        "snapshot_data": row.snapshot_data,
        "fingerprint": row.fingerprint,
        "schema_version": row.schema_version,
        "created_at": row.created_at.isoformat(),
    }


def _proposal(row) -> dict:
    return {
        "id": str(row.id),
        "profile_id": str(row.profile_id),
        "snapshot_id": str(row.snapshot_id),
        "proposed_patch": row.proposed_patch,
        "status": row.status.value,
        "decided_by_id": str(row.decided_by_id) if row.decided_by_id else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/raw-artifacts/{artifact_id}/enrich", status_code=status.HTTP_201_CREATED)
async def enrich_raw_artifact(
    artifact_id: uuid.UUID,
    payload: EnrichRawArtifactRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict:
    service = IntelligenceService(session, workspace_id, current_user.id)
    item, snapshot, proposal = await service.enrich_artifact_for_profile(
        artifact_id,
        payload.profile_id,
        ai_engine,
    )
    return success_response(
        request,
        {
            "item": service.serialize_item(item, include_content=True),
            "snapshot": _snapshot(snapshot),
            "proposal": _proposal(proposal) if proposal else None,
        },
    )


@router.post("/search-runs", status_code=status.HTTP_201_CREATED)
async def create_search_run(
    payload: CreateSearchRunRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).create_run(
        query=payload.query,
        purpose=payload.purpose,
        provider=payload.provider,
        sources=payload.sources,
    )
    return success_response(request, _run(row))


@router.get("/search-runs")
async def list_search_runs(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    rows, total = await IntelligenceService(session, workspace_id, current_user.id).list_runs(
        page, page_size
    )
    return success_response(
        request,
        {
            "items": [_run(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/search-runs/{run_id}")
async def get_search_run(
    run_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).get_run(run_id)
    return success_response(request, _run(row))


@router.post("/search-runs/{run_id}/enrichment-jobs", status_code=status.HTTP_202_ACCEPTED)
async def queue_provider_enrichment(
    run_id: uuid.UUID,
    payload: QueueProviderEnrichmentRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    provider_request = EnrichmentJobRequest(
        client_job_id=run_id,
        company_name=payload.company_name,
        website_url=payload.website_url,
        allowed_fields=payload.allowed_fields,
        language=payload.language,
        country=payload.country,
        max_tool_calls=payload.max_tool_calls,
        max_cost_usd=payload.max_cost_usd,
    )
    task = await IntelligenceService(
        session, workspace_id, current_user.id
    ).queue_provider_enrichment(run_id, payload.profile_id, provider_request)
    return success_response(
        request,
        {
            "task_id": str(task.id),
            "run_id": str(task.target_id),
            "status": task.status.value,
            "stage": task.stage,
            "trace_id": task.trace_id,
        },
    )


@router.get("/items")
async def list_intelligence_items(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    run_id: uuid.UUID | None = None,
    freshness: IntelligenceFreshness | None = None,
    conflict_group_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    service = IntelligenceService(session, workspace_id, current_user.id)
    rows, total = await service.list_items(
        page,
        page_size,
        run_id,
        freshness=freshness,
        conflict_group_id=conflict_group_id,
    )
    return success_response(
        request,
        {
            "items": [service.serialize_item(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/items/{item_id}")
async def get_intelligence_item(
    item_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    service = IntelligenceService(session, workspace_id, current_user.id)
    row = await service.get_item(item_id)
    return success_response(request, service.serialize_item(row, include_content=True))


@router.post("/items/reassess-freshness")
async def reassess_intelligence_freshness(
    payload: ReassessIntelligenceFreshnessRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    result = await IntelligenceService(session, workspace_id, current_user.id).reassess_freshness(
        stale_after_days=payload.stale_after_days
    )
    return success_response(request, result)


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
async def create_intelligence_snapshot(
    payload: CreateIntelligenceSnapshotRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).create_snapshot(
        payload.purpose, payload.item_ids
    )
    return success_response(request, _snapshot(row))


@router.get("/snapshots/{snapshot_id}")
async def get_intelligence_snapshot(
    snapshot_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).get_snapshot(
        snapshot_id
    )
    return success_response(request, _snapshot(row))


@profile_router.post("/{profile_id}/intelligence-proposals", status_code=status.HTTP_201_CREATED)
async def create_profile_proposal(
    profile_id: uuid.UUID,
    payload: CreateProfileProposalRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).create_profile_proposal(
        profile_id, payload.snapshot_id, payload.proposed_patch
    )
    return success_response(request, _proposal(row))


@profile_router.post("/{profile_id}/intelligence-proposals/{proposal_id}/confirm")
async def confirm_profile_proposal(
    profile_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: DecideProposalRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).decide_proposal(
        profile_id,
        proposal_id,
        accept=True,
        note=payload.note,
        selected_candidates=payload.selected_candidates,
    )
    return success_response(request, _proposal(row))


@profile_router.post("/{profile_id}/intelligence-proposals/{proposal_id}/reject")
async def reject_profile_proposal(
    profile_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: DecideProposalRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await IntelligenceService(session, workspace_id, current_user.id).decide_proposal(
        profile_id, proposal_id, accept=False, note=payload.note
    )
    return success_response(request, _proposal(row))
