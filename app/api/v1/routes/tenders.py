import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import CurrentUser, DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.v2 import (
    CreateResponseMatrixRequest,
    CreateTenderRequest,
    ReviewResponseItemRequest,
    UpdateResponseItemRequest,
)
from app.services.tender_service import TenderService

router = APIRouter(route_class=IdempotencyRoute)
matrix_router = APIRouter(route_class=IdempotencyRoute)


def _tender(row) -> dict:
    return {
        "id": str(row.id),
        "title": row.title,
        "customer_profile_id": str(row.customer_profile_id) if row.customer_profile_id else None,
        "source_filename": row.source_filename,
        "source_mime_type": row.source_mime_type,
        "source_fingerprint": row.source_fingerprint,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _requirement(row) -> dict:
    return {
        "id": str(row.id),
        "tender_id": str(row.tender_id),
        "sequence": row.sequence,
        "requirement_text": row.requirement_text,
        "category": row.category,
        "mandatory": row.mandatory,
        "source_location": row.source_location,
    }


def _item(row) -> dict:
    return {
        "id": str(row.id),
        "matrix_id": str(row.matrix_id),
        "requirement_id": str(row.requirement_id),
        "response_text": row.response_text,
        "evidence_status": row.evidence_status.value,
        "evidence_refs": row.evidence_refs,
        "risks": row.risks,
        "review_status": row.review_status,
        "reviewer_id": str(row.reviewer_id) if row.reviewer_id else None,
        "review_note": row.review_note,
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tender(
    payload: CreateTenderRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).create_tender(
        title=payload.title,
        customer_profile_id=payload.customer_profile_id,
        pasted_text=payload.pasted_text,
        content_base64=payload.content_base64,
        filename=payload.source_filename,
        mime_type=payload.source_mime_type,
    )
    return success_response(request, _tender(row))


@router.get("")
async def list_tenders(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    rows, total = await TenderService(session, workspace_id, current_user.id).list_tenders(
        page, page_size
    )
    return success_response(
        request,
        {
            "items": [_tender(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{tender_id}")
async def get_tender(
    tender_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).get_tender(tender_id)
    return success_response(request, _tender(row))


@router.get("/{tender_id}/requirements")
async def get_tender_requirements(
    tender_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    rows = await TenderService(session, workspace_id, current_user.id).list_requirements(tender_id)
    return success_response(
        request, {"items": [_requirement(row) for row in rows], "total": len(rows)}
    )


@router.post("/{tender_id}/response-matrices", status_code=status.HTTP_201_CREATED)
async def create_response_matrix(
    tender_id: uuid.UUID,
    payload: CreateResponseMatrixRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    service = TenderService(session, workspace_id, current_user.id)
    row = await service.create_matrix(
        tender_id, payload.experience_ids, payload.capability_ids, payload.intelligence_snapshot_id
    )
    matrix, items = await service.get_matrix(row.id)
    return success_response(
        request,
        {
            "id": str(matrix.id),
            "tender_id": str(matrix.tender_id),
            "version": matrix.version,
            "status": matrix.status,
            "evidence_snapshot": matrix.evidence_snapshot,
            "items": [_item(item) for item in items],
        },
    )


@matrix_router.get("/{matrix_id}")
async def get_response_matrix(
    matrix_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    matrix, items = await TenderService(session, workspace_id, current_user.id).get_matrix(
        matrix_id
    )
    return success_response(
        request,
        {
            "id": str(matrix.id),
            "tender_id": str(matrix.tender_id),
            "version": matrix.version,
            "status": matrix.status,
            "evidence_snapshot": matrix.evidence_snapshot,
            "items": [_item(item) for item in items],
        },
    )


@matrix_router.patch("/{matrix_id}/items/{item_id}")
async def update_response_matrix_item(
    matrix_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: UpdateResponseItemRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).update_item(
        matrix_id, item_id, payload.response_text, payload.risks
    )
    return success_response(request, _item(row))


@matrix_router.post("/{matrix_id}/items/{item_id}/review")
async def review_response_matrix_item(
    matrix_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ReviewResponseItemRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).review_item(
        matrix_id, item_id, payload.action, payload.note
    )
    return success_response(request, _item(row))
