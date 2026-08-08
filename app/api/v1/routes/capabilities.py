import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentUser, DatabaseSession, EmbeddingProviderDependency, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.models import ReviewStatus
from app.schemas.assets import CapabilityData, CapabilityRead, ReviewRequest
from app.services.asset_service import CapabilityService

router = APIRouter(route_class=IdempotencyRoute)


def serialize_capability(asset) -> dict:
    return CapabilityRead.model_validate(asset).model_dump(mode="json")


@router.get("")
async def list_capabilities(
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=200)] = None,
    review_status: ReviewStatus | None = None,
) -> dict[str, object]:
    items, total = await CapabilityService(session, workspace_id).list_assets(
        page=page,
        page_size=page_size,
        keyword=keyword,
        review_status=review_status,
    )
    return success_response(
        request,
        {
            "items": [serialize_capability(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@router.get("/{capability_id}")
async def get_capability(
    capability_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    asset = await CapabilityService(session, workspace_id).get(capability_id)
    return success_response(request, serialize_capability(asset))


@router.patch("/{capability_id}")
async def update_capability(
    capability_id: uuid.UUID,
    payload: CapabilityData,
    request: Request,
    session: DatabaseSession,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    asset = await CapabilityService(session, workspace_id).update(
        capability_id, payload.model_dump(mode="json")
    )
    return success_response(request, serialize_capability(asset))


@router.post("/{capability_id}/review")
async def review_capability(
    capability_id: uuid.UUID,
    payload: ReviewRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    embedding_provider: EmbeddingProviderDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    asset = await CapabilityService(
        session, workspace_id, current_user.id, embedding_provider
    ).review(capability_id, payload.action, payload.note)
    return success_response(request, serialize_capability(asset))
