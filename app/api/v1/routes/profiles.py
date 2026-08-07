import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.assets import (
    CreateProfileRequest,
    CustomerProfileRead,
    GenerateProfileRequest,
    UpdateProfileRequest,
)
from app.services.profile_service import CustomerProfileService

router = APIRouter(route_class=IdempotencyRoute)


def serialize_profile(profile) -> dict:
    return CustomerProfileRead.model_validate(profile).model_dump(mode="json")


@router.post("")
async def create_profile(
    payload: CreateProfileRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    profile, created = await CustomerProfileService(session, workspace_id).create(
        payload.customer_name
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(request, serialize_profile(profile))


@router.get("")
async def list_profiles(
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=200)] = None,
) -> dict[str, object]:
    items, total = await CustomerProfileService(session, workspace_id).list_profiles(
        page=page, page_size=page_size, keyword=keyword
    )
    return success_response(
        request,
        {
            "items": [serialize_profile(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    profile = await CustomerProfileService(session, workspace_id).get(profile_id)
    return success_response(request, serialize_profile(profile))


@router.patch("/{profile_id}")
async def update_profile(
    profile_id: uuid.UUID,
    payload: UpdateProfileRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    profile = await CustomerProfileService(session, workspace_id).update(
        profile_id,
        customer_name=payload.customer_name,
        profile_data=payload.profile.model_dump(mode="json"),
        source_ids=payload.source_ids,
    )
    return success_response(request, serialize_profile(profile))


@router.post("/{profile_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_profile(
    profile_id: uuid.UUID,
    payload: GenerateProfileRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    job = await CustomerProfileService(session, workspace_id).generate(
        profile_id,
        source_ids=payload.source_ids,
        supplemental_text=payload.supplemental_text,
    )
    return success_response(
        request,
        {
            "job_id": str(job.id),
            "target_id": str(profile_id),
            "status": job.status.value,
            "poll_after_ms": 1000,
        },
    )


@router.post("/{profile_id}/confirm")
async def confirm_profile(
    profile_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    await CustomerProfileService(session, workspace_id).confirm(profile_id)
    return success_response(request, {"success": True})
