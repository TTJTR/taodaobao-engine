import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.ai.embedding import AssetType
from app.api.deps import (
    CurrentUser,
    DatabaseSession,
    WorkspaceId,
    bailian_is_configured,
    get_ai_engine,
    get_embedding_provider,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
from app.schemas.model_connections import ConfigureModelConnectionRequest
from app.services.model_connection_service import (
    CAPABILITIES,
    bailian_search_is_configured,
    catalog_payload,
    configure_workspace_connection,
    connection_payload,
    delete_workspace_connection,
    get_workspace_connection,
    test_candidate,
    workspace_search_provider,
)
from app.services.runtime_service import RuntimeService

runtime_router = APIRouter()
connections_router = APIRouter(route_class=IdempotencyRoute)


@runtime_router.get("/tasks")
async def list_runtime_tasks(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    task_type: str | None = None,
    task_status: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    rows, total = await RuntimeService(session, workspace_id).list_tasks(
        page=page, page_size=page_size, task_type=task_type, status=task_status
    )
    return success_response(
        request,
        {
            "items": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
        },
    )


@runtime_router.get("/tasks/{task_id}")
async def get_runtime_task(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    return success_response(request, await RuntimeService(session, workspace_id).detail(task_id))


@runtime_router.get("/tasks/{task_id}/steps")
async def get_runtime_task_steps(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    rows = await RuntimeService(session, workspace_id).steps(task_id)
    return success_response(request, {"items": rows, "total": len(rows)})


async def _connections(session: DatabaseSession, workspace_id: uuid.UUID) -> list[dict]:
    items = [
        {
            "provider": "ai",
            "label": "AI 可信服务",
            "mode": settings.ai_mode,
            "status": "configured"
            if settings.ai_mode == "live" and bailian_is_configured()
            else "not_configured",
            "configurable": True,
            "source": "default",
        },
        {
            "provider": "deep-research",
            "label": "Deep Research 编排",
            "mode": "internal",
            "status": "configured",
            "configurable": False,
            "source": "inherits-ai",
        },
        {
            "provider": "interactive-html",
            "label": "互动 HTML 生成",
            "mode": settings.interactive_html_mode,
            "status": "configured"
            if settings.interactive_html_mode == "live" and settings.interactive_html_api_key
            else "not_configured",
            "configurable": True,
            "source": "default",
        },
        {
            "provider": "web-search",
            "label": "公开情报搜索",
            "mode": "live",
            "status": "disabled"
            if not settings.enable_public_intelligence
            else "configured"
            if bailian_search_is_configured()
            else "not_configured",
            "configurable": True,
            "source": "default",
            "note": "百炼只负责发现公开来源；正文抓取、哈希、快照与人工审批由本系统完成。",
        },
        {
            "provider": "embedding",
            "label": "资料向量索引",
            "mode": settings.ai_mode,
            "status": "configured"
            if settings.ai_mode == "live" and bailian_is_configured()
            else "not_configured",
            "configurable": False,
            "source": "server-fixed",
            "note": "向量模型与历史索引维度绑定；切换前必须执行全量重建索引。",
        },
        {
            "provider": "worker",
            "label": "持久化任务 Worker",
            "mode": "internal",
            "status": "configured" if settings.database_url else "not_configured",
            "configurable": False,
            "source": "server",
        },
        {
            "provider": "feishu",
            "label": "飞书开放平台",
            "mode": settings.feishu_mode,
            "status": "disabled"
            if not settings.enable_feishu
            else "configured"
            if settings.feishu_mode == "live"
            and settings.feishu_app_id
            and settings.feishu_app_secret
            else "not_configured",
            "configurable": False,
            "source": "server",
        },
    ]
    for capability in CAPABILITIES:
        selected = await get_workspace_connection(session, workspace_id, capability)
        if selected is None:
            continue
        target = next(item for item in items if item["provider"] == capability)
        target.update(connection_payload(selected))
        target["status"] = "configured"
        target["mode"] = "live"
    return items


@connections_router.get("")
async def list_model_connections(
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
) -> dict:
    return success_response(request, {"items": await _connections(session, workspace_id)})


@connections_router.get("/catalog")
async def get_model_connection_catalog(request: Request, current_user: CurrentUser) -> dict:
    return success_response(request, {"items": catalog_payload()})


@connections_router.put("/{capability}/credential")
async def put_model_connection_credential(
    capability: str,
    payload: ConfigureModelConnectionRequest,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    row = await configure_workspace_connection(
        session,
        workspace_id,
        current_user.id,
        capability,
        payload.provider,
        payload.model,
        payload.api_key,
    )
    return success_response(request, connection_payload(row))


@connections_router.delete("/{capability}/credential")
async def remove_model_connection_credential(
    capability: str,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    workspace_id: WorkspaceId,
    _: str = Depends(require_idempotency_key),
) -> dict:
    if capability not in CAPABILITIES:
        raise AppError(ErrorCode.VALIDATION_FAILED, "未知模型能力", status_code=404)
    deleted = await delete_workspace_connection(session, workspace_id, capability)
    return success_response(
        request,
        {"capability": capability, "deleted": deleted, "source": "default"},
    )


@connections_router.post("/{provider}/test")
async def test_model_connection(
    provider: str,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict:
    known = {row["provider"]: row for row in await _connections(session, current_user.workspace_id)}
    if provider not in known:
        raise AppError(ErrorCode.VALIDATION_FAILED, "未知服务连接", status_code=404)
    started = time.perf_counter()
    mode = known[provider]["mode"]
    selected = (
        await get_workspace_connection(session, current_user.workspace_id, provider)
        if provider in CAPABILITIES
        else None
    )
    try:
        if selected is not None:
            latency_ms = await test_candidate(
                selected.provider,
                selected.model,
                selected.api_key,
                selected.capability,
            )
        elif provider == "ai":
            ai_engine = get_ai_engine()
            result = await ai_engine.extract_search_intent(
                {
                    "customer_profile": {
                        "customer_name": "连接测试客户",
                        "profile_summary": "仅用于检查模型连接，不产生业务数据。",
                        "source_ids": ["connection-test"],
                    },
                    "current_requirement": "检查 AI 服务是否能够返回合法 Schema",
                    "trace_id": f"connection-test-{provider}",
                }
            )
            if not isinstance(result, dict):
                raise RuntimeError("AI returned non-object output")
        elif provider in {"worker", "deep-research"}:
            await session.execute(text("SELECT 1"))
        elif provider == "embedding":
            result = await get_embedding_provider().embed(
                "淘到宝资料索引真实连接测试",
                AssetType.QUERY,
            )
            if not result.vector:
                raise RuntimeError("embedding provider returned an empty vector")
        elif provider == "feishu" and settings.feishu_mode == "live":
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": settings.feishu_app_id,
                        "app_secret": settings.feishu_app_secret,
                    },
                )
                response.raise_for_status()
                if response.json().get("code") != 0:
                    raise RuntimeError("Feishu rejected the configured application credentials")
        elif provider == "presentation" and settings.presentation_mode == "live":
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{settings.presentation_service_url.rstrip('/')}/health"
                )
                response.raise_for_status()
        elif provider == "interactive-html" and settings.interactive_html_mode == "live":
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{settings.interactive_html_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {settings.interactive_html_api_key}"},
                )
                response.raise_for_status()
        elif provider == "web-search":
            search_provider = await workspace_search_provider(session, current_user.workspace_id)
            if search_provider is None:
                raise RuntimeError("web search is not configured")
            try:
                await search_provider.search("阿里云官网", max_results=1)
            finally:
                await search_provider.aclose()
        else:
            raise RuntimeError("provider is not configured or not independently testable")
    except Exception as exc:
        raise AppError(
            ErrorCode.MODEL_CONNECTION_UNAVAILABLE,
            "服务连接测试失败",
            status_code=503,
            retryable=True,
            details={"provider": provider, "reason": type(exc).__name__},
        ) from exc
    return success_response(
        request,
        {
            "provider": provider,
            "status": "ok",
            "mode": mode,
            "latency_ms": latency_ms
            if selected is not None
            else round((time.perf_counter() - started) * 1000),
            "tested_at": datetime.now(UTC).isoformat(),
        },
    )
