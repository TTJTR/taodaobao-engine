from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from app.api.deps import CurrentUser, DatabaseSession, FeishuAdapterDependency, WorkspaceId
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.models import SourceStatus
from app.schemas.sources import ImportLinkRequest, ImportTextRequest, SourceRead
from app.services.job_runner import run_source_job
from app.services.source_service import SourceService

router = APIRouter(route_class=IdempotencyRoute)


@router.post("/import-link", status_code=status.HTTP_202_ACCEPTED)
async def import_link(
    payload: ImportLinkRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    result = await SourceService(session, workspace_id, current_user.id).import_link(
        url=str(payload.url),
        purpose=payload.purpose,
        customer_profile_id=payload.customer_profile_id,
    )
    background_tasks.add_task(
        run_source_job, result.job.id, result.source.id, workspace_id, adapter
    )
    return success_response(
        request,
        {
            "job_id": str(result.job.id),
            "target_id": str(result.source.id),
            "status": result.job.status.value,
            "poll_after_ms": 1000,
        },
    )


@router.post("/import-text", status_code=status.HTTP_202_ACCEPTED)
async def import_text(
    payload: ImportTextRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    result = await SourceService(session, workspace_id, current_user.id).import_text(
        title=payload.title,
        content=payload.content,
        purpose=payload.purpose,
        customer_profile_id=payload.customer_profile_id,
    )
    background_tasks.add_task(
        run_source_job, result.job.id, result.source.id, workspace_id, adapter
    )
    return success_response(
        request,
        {
            "job_id": str(result.job.id),
            "target_id": str(result.source.id),
            "status": result.job.status.value,
            "poll_after_ms": 1000,
        },
    )


@router.get("")
async def list_sources(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[SourceStatus | None, Query(alias="status")] = None,
) -> dict[str, object]:
    items, total = await SourceService(session, workspace_id, current_user.id).list_sources(
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status_filter,
    )
    return success_response(
        request,
        {
            "items": [SourceRead.model_validate(item).model_dump(mode="json") for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )
