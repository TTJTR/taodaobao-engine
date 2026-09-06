import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status

from app.api.deps import (
    AIEngineDependency,
    CurrentUser,
    DatabaseSession,
    FeishuAdapterDependency,
    WorkspaceId,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.db.models import SourcePurpose, SourceStatus
from app.schemas.sources import (
    ImportLinkRequest,
    ImportTextRequest,
    SourceRead,
    UpdateSourceRequest,
)
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
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    result = await SourceService(session, workspace_id, current_user.id).import_link(
        url=str(payload.url),
        purpose=payload.purpose,
        customer_profile_id=payload.customer_profile_id,
    )
    background_tasks.add_task(
        run_source_job,
        result.job.id,
        result.source.id,
        workspace_id,
        adapter,
        ai_engine,
        current_user.feishu_access_token,
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
    ai_engine: AIEngineDependency,
    demo_header: Annotated[str | None, Header(alias="X-Demo-Data")] = None,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    is_demo = (demo_header or "").strip().lower() in {"1", "true", "yes"}
    if is_demo and not settings.enable_demo_data:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "演示资料导入未启用",
            status_code=503,
        )
    result = await SourceService(session, workspace_id, current_user.id).import_text(
        title=payload.title,
        content=payload.content,
        purpose=payload.purpose,
        customer_profile_id=payload.customer_profile_id,
        is_demo=is_demo,
    )
    background_tasks.add_task(
        run_source_job,
        result.job.id,
        result.source.id,
        workspace_id,
        None,
        ai_engine,
        current_user.feishu_access_token,
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
    purpose: SourcePurpose | None = None,
) -> dict[str, object]:
    items, total = await SourceService(session, workspace_id, current_user.id).list_sources(
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status_filter,
        purpose=purpose,
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


@router.get("/{source_id}")
async def get_source(
    source_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict[str, object]:
    source = await SourceService(session, workspace_id, current_user.id).get(source_id)
    return success_response(request, SourceRead.model_validate(source).model_dump(mode="json"))


@router.delete("/{source_id}")
async def delete_source(
    source_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    await SourceService(session, workspace_id, current_user.id).delete(source_id)
    return success_response(request, {"success": True})


@router.patch("/{source_id}")
async def update_source(
    source_id: uuid.UUID,
    payload: UpdateSourceRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    background_tasks: BackgroundTasks,
    adapter: FeishuAdapterDependency,
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    source, job = await SourceService(session, workspace_id, current_user.id).update(
        source_id,
        title=payload.title,
        purpose=payload.purpose,
        tags=payload.tags,
    )
    if job is not None:
        background_tasks.add_task(
            run_source_job,
            job.id,
            source.id,
            workspace_id,
            adapter,
            ai_engine,
            current_user.feishu_access_token,
        )
    return success_response(request, SourceRead.model_validate(source).model_dump(mode="json"))


async def _enqueue_source_action(
    source_id,
    action,
    background_tasks,
    session,
    current_user,
    workspace_id,
    adapter,
    ai_engine,
):
    result = await action(SourceService(session, workspace_id, current_user.id), source_id)
    background_tasks.add_task(
        run_source_job,
        result.job.id,
        result.source.id,
        workspace_id,
        adapter,
        ai_engine,
        current_user.feishu_access_token,
    )
    return result


@router.post("/{source_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_source(
    source_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    result = await _enqueue_source_action(
        source_id,
        lambda service, item_id: service.refresh(item_id),
        background_tasks,
        session,
        current_user,
        workspace_id,
        adapter,
        ai_engine,
    )
    return success_response(
        request,
        {"job_id": str(result.job.id), "target_id": str(result.source.id), "status": "pending"},
    )


@router.post("/{source_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_source(
    source_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    result = await _enqueue_source_action(
        source_id,
        lambda service, item_id: service.retry(item_id),
        background_tasks,
        session,
        current_user,
        workspace_id,
        adapter,
        ai_engine,
    )
    return success_response(
        request,
        {"job_id": str(result.job.id), "target_id": str(result.source.id), "status": "pending"},
    )
