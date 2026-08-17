from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentUser, DatabaseSession, FeishuAdapterDependency, WorkspaceId
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import require_idempotency_key
from app.core.responses import success_response
from app.schemas.v1 import FeishuResourceRead
from app.services.bitable_sync_service import BitableSyncService

router = APIRouter()


@router.get("/bitable/sync-status")
async def get_bitable_sync_status(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
) -> dict[str, object]:
    result = BitableSyncService(
        session,
        workspace_id,
        adapter,
        current_user.feishu_access_token,
    ).sync_status()
    return success_response(request, result)


@router.post("/bitable/sync-daily")
async def sync_daily_to_bitable(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    adapter: FeishuAdapterDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    try:
        result = await BitableSyncService(
            session,
            workspace_id,
            adapter,
            current_user.feishu_access_token,
        ).sync_daily()
    except Exception as exc:
        raise AppError(
            ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE,
            "多维表格同步失败，请检查应用权限或重新进行飞书授权",
            status_code=503,
            retryable=True,
            details={"reason": str(exc)[:500]},
        ) from exc
    return success_response(request, result)


@router.get("/resources")
async def list_feishu_resources(
    request: Request,
    current_user: CurrentUser,
    adapter: FeishuAdapterDependency,
    resource_type: Literal["document", "minute"] = Query(alias="type"),
    page_token: str | None = Query(default=None, max_length=512),
) -> dict[str, object]:
    items, next_page_token = await adapter.list_resources(
        resource_type, current_user.feishu_access_token, page_token
    )
    return success_response(
        request,
        {
            "items": [
                FeishuResourceRead.model_validate(item, from_attributes=True).model_dump(
                    mode="json"
                )
                for item in items
            ],
            "next_page_token": next_page_token,
        },
    )
