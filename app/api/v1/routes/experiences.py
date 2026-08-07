import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DatabaseSession, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.models import ReviewStatus
from app.schemas.assets import ExperienceData, ExperienceRead, ReviewRequest
from app.services.asset_service import ExperienceService

router = APIRouter(route_class=IdempotencyRoute)


def serialize_experience(asset) -> dict:
    return ExperienceRead.model_validate(asset).model_dump(mode="json")


@router.get("")
async def list_experiences(
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=200)] = None,
    review_status: ReviewStatus | None = None,
) -> dict[str, object]:
    items, total = await ExperienceService(session, workspace_id).list_assets(
        page=page,
        page_size=page_size,
        keyword=keyword,
        review_status=review_status,
    )
    return success_response(
        request,
        {
            "items": [serialize_experience(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{experience_id}")
async def get_experience(
    experience_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    asset = await ExperienceService(session, workspace_id).get(experience_id)
    return success_response(request, serialize_experience(asset))


@router.patch("/{experience_id}")
async def update_experience(
    experience_id: uuid.UUID,
    payload: ExperienceData,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    asset = await ExperienceService(session, workspace_id).update(
        experience_id, payload.model_dump(mode="json")
    )
    return success_response(request, serialize_experience(asset))


@router.post("/{experience_id}/review")
async def review_experience(
    experience_id: uuid.UUID,
    payload: ReviewRequest,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    asset = await ExperienceService(session, workspace_id).review(
        experience_id, payload.action, payload.note
    )
    return success_response(request, serialize_experience(asset))
