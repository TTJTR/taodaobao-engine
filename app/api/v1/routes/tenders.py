import csv
import io
import uuid
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.deps import CurrentUser, DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.v2 import (
    BatchReviewResponseItemsRequest,
    CreateResponseMatrixRequest,
    CreateTenderRequest,
    MergeTenderRequirementsRequest,
    QueueTenderParseRequest,
    RequirementVersionRequest,
    ReviewResponseItemRequest,
    SplitTenderRequirementRequest,
    UpdateResponseItemRequest,
    UpdateTenderRequirementRequest,
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
        "is_mandatory": row.mandatory,
        "source_location": row.source_location,
        "version": row.version,
        "status": row.status.value,
        "acceptance_condition": row.acceptance_condition,
        "constraints": row.constraints,
        "metrics": row.metrics,
        "ambiguities": row.ambiguities,
        "recommended_action": row.recommended_action,
        "confirmed_by_id": str(row.confirmed_by_id) if row.confirmed_by_id else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }


def _item(row) -> dict:
    return {
        "id": str(row.id),
        "matrix_id": str(row.matrix_id),
        "requirement_id": str(row.requirement_id),
        "response_text": row.response_text,
        "ai_draft": row.ai_draft,
        "current_answer": row.current_answer,
        "evidence_status": row.evidence_status.value,
        "evidence_refs": row.evidence_refs,
        "risks": row.risks,
        "internal_exp_links": row.internal_exp_links,
        "internal_cap_links": row.internal_cap_links,
        "external_ctx_links": row.external_ctx_links,
        "risk_flags": row.risk_flags,
        "review_status": row.review_status,
        "reviewer_id": str(row.reviewer_id) if row.reviewer_id else None,
        "review_note": row.review_note,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
    }


def _item_version(row) -> dict:
    return {
        "id": str(row.id),
        "response_item_id": str(row.response_item_id),
        "version": row.version,
        "changed_by_id": str(row.changed_by_id) if row.changed_by_id else None,
        "change_type": row.change_type,
        "item_snapshot": row.item_snapshot,
        "created_at": row.created_at.isoformat(),
    }


def _matrix_summary(row, item_count: int) -> dict:
    return {
        "id": str(row.id),
        "tender_id": str(row.tender_id),
        "version": row.version,
        "status": row.status,
        "item_count": item_count,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _links_text(links: list[dict]) -> str:
    return "; ".join(str(item.get("asset_id") or item.get("snapshot_id") or "") for item in links)


def _matrix_csv(rows) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "requirement",
            "category",
            "mandatory",
            "source_location",
            "ai_draft",
            "current_answer",
            "internal_experience_ids",
            "internal_capability_ids",
            "external_context_ids",
            "risk_flags",
            "evidence_status",
            "review_status",
        ]
    )
    for item, requirement in rows:
        writer.writerow(
            [
                requirement.requirement_text,
                requirement.category,
                requirement.mandatory,
                str(requirement.source_location),
                item.ai_draft,
                item.current_answer,
                _links_text(item.internal_exp_links),
                _links_text(item.internal_cap_links),
                _links_text(item.external_ctx_links),
                "; ".join(item.risk_flags),
                item.evidence_status.value,
                item.review_status,
            ]
        )
    return "\ufeff" + stream.getvalue()


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


@router.post(
    "/file-uploads",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime_type: {"schema": {"type": "string", "format": "binary"}}
                for mime_type in (
                    "application/pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text/plain",
                )
            },
        }
    },
)
async def upload_tender_file(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    title: Annotated[str, Query(min_length=1, max_length=500)],
    x_file_name: Annotated[str, Header(alias="X-File-Name", min_length=1, max_length=1500)],
    customer_profile_id: uuid.UUID | None = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    _: str = Depends(require_idempotency_key),
) -> dict:
    mime_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    service = TenderService(session, workspace_id, current_user.id)
    tender, artifact = await service.create_uploaded_tender(
        title=title,
        customer_profile_id=customer_profile_id,
        payload=await request.body(),
        filename=unquote(x_file_name),
        mime_type=mime_type,
    )
    task = await service.queue_parse(tender.id, artifact.id)
    return success_response(
        request,
        {
            "tender": _tender(tender),
            "artifact": {
                "id": str(artifact.id),
                "filename": artifact.source_filename,
                "mime_type": artifact.mime_type,
                "content_sha256": artifact.content_sha256,
                "byte_size": artifact.byte_size,
                "status": artifact.status.value,
            },
            "task": {
                "id": str(task.id),
                "status": task.status.value,
                "stage": task.stage,
                "trace_id": task.trace_id,
            },
        },
    )


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


@router.post("/{tender_id}/parse-tasks", status_code=status.HTTP_202_ACCEPTED)
async def queue_tender_parse(
    tender_id: uuid.UUID,
    payload: QueueTenderParseRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    task = await TenderService(session, workspace_id, current_user.id).queue_parse(
        tender_id, payload.raw_artifact_id
    )
    return success_response(
        request,
        {
            "task_id": str(task.id),
            "tender_id": str(task.target_id),
            "raw_artifact_id": task.payload["raw_artifact_id"],
            "status": task.status.value,
            "stage": task.stage,
            "trace_id": task.trace_id,
        },
    )


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


@router.post("/{tender_id}/requirements/breakdown", status_code=status.HTTP_201_CREATED)
async def breakdown_tender_requirements(
    tender_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    rows = await TenderService(session, workspace_id, current_user.id).breakdown_requirements(
        tender_id
    )
    return success_response(
        request, {"items": [_requirement(row) for row in rows], "total": len(rows)}
    )


@router.patch("/{tender_id}/requirements/{requirement_id}")
async def update_tender_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: UpdateTenderRequirementRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
    row = await TenderService(session, workspace_id, current_user.id).update_requirement(
        tender_id, requirement_id, payload.expected_version, **changes
    )
    return success_response(request, _requirement(row))


@router.post("/{tender_id}/requirements/{requirement_id}/confirm")
async def confirm_tender_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: RequirementVersionRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).confirm_requirement(
        tender_id, requirement_id, payload.expected_version
    )
    return success_response(request, _requirement(row))


@router.delete("/{tender_id}/requirements/{requirement_id}")
async def delete_tender_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: RequirementVersionRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    await TenderService(session, workspace_id, current_user.id).delete_requirement(
        tender_id, requirement_id, payload.expected_version
    )
    return success_response(request, {"deleted": True, "id": str(requirement_id)})


@router.post("/{tender_id}/requirements/merge")
async def merge_tender_requirements(
    tender_id: uuid.UUID,
    payload: MergeTenderRequirementsRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await TenderService(session, workspace_id, current_user.id).merge_requirements(
        tender_id, payload.requirement_ids, payload.expected_versions, payload.requirement_text
    )
    return success_response(request, _requirement(row))


@router.post("/{tender_id}/requirements/{requirement_id}/split", status_code=201)
async def split_tender_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: SplitTenderRequirementRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    rows = await TenderService(session, workspace_id, current_user.id).split_requirement(
        tender_id, requirement_id, payload.expected_version, payload.items
    )
    return success_response(request, {"items": [_requirement(row) for row in rows]})


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


@router.get("/{tender_id}/response-matrices")
async def list_response_matrices(
    tender_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    rows, total = await TenderService(session, workspace_id, current_user.id).list_matrices(
        tender_id, page, page_size
    )
    return success_response(
        request,
        {
            "items": [_matrix_summary(row, item_count) for row, item_count in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
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
        matrix_id, item_id, payload.response_text, payload.risks, payload.expected_version
    )
    return success_response(request, _item(row))


@matrix_router.get("/{matrix_id}/items/{item_id}/versions")
async def list_response_matrix_item_versions(
    matrix_id: uuid.UUID,
    item_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    rows = await TenderService(session, workspace_id, current_user.id).list_item_versions(
        matrix_id, item_id
    )
    return success_response(
        request,
        {"items": [_item_version(row) for row in rows], "total": len(rows)},
    )


@matrix_router.get("/{matrix_id}/items")
async def list_response_matrix_items(
    matrix_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    category: str | None = None,
    evidence_status: str | None = None,
    review_status: str | None = None,
    risk_flag: str | None = None,
) -> dict:
    _, rows = await TenderService(session, workspace_id, current_user.id).list_matrix_items(
        matrix_id,
        category=category,
        evidence_status=evidence_status,
        review_status=review_status,
        risk_flag=risk_flag,
    )
    return success_response(
        request,
        {
            "items": [
                {**_item(item), "requirement": _requirement(requirement)}
                for item, requirement in rows
            ],
            "total": len(rows),
        },
    )


@matrix_router.get("/{matrix_id}/export.csv")
async def export_response_matrix_csv(
    matrix_id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    category: str | None = None,
    evidence_status: str | None = None,
    review_status: str | None = None,
    risk_flag: str | None = None,
) -> Response:
    _, rows = await TenderService(session, workspace_id, current_user.id).list_matrix_items(
        matrix_id,
        category=category,
        evidence_status=evidence_status,
        review_status=review_status,
        risk_flag=risk_flag,
    )
    return Response(
        content=_matrix_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="response-matrix-{matrix_id}.csv"'},
    )


@matrix_router.get("/{matrix_id}/export")
async def export_response_matrix(
    matrix_id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> Response:
    _, rows = await TenderService(session, workspace_id, current_user.id).list_matrix_items(
        matrix_id
    )
    return Response(
        content=_matrix_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="response-matrix-{matrix_id}.csv"'},
    )


@matrix_router.post("/{matrix_id}/batch-review")
async def batch_review_response_matrix_items(
    matrix_id: uuid.UUID,
    payload: BatchReviewResponseItemsRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    rows = await TenderService(session, workspace_id, current_user.id).batch_review_items(
        matrix_id, payload.item_ids, payload.action, payload.expected_versions, payload.note
    )
    return success_response(request, {"items": [_item(row) for row in rows], "total": len(rows)})


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
        matrix_id,
        item_id,
        payload.action,
        payload.note,
        payload.expected_version,
        payload.current_answer,
    )
    return success_response(request, _item(row))
