import time
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.api.deps import AIEngineDependency, CurrentUser, DatabaseSession, WorkspaceId
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response
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


def _connections() -> list[dict]:
    return [
        {
            "provider": "ai",
            "label": "AI 可信服务",
            "mode": settings.ai_mode,
            "status": "mock" if settings.ai_mode == "mock" else "configured",
        },
        {
            "provider": "deep-research",
            "label": "Deep Research 编排",
            "mode": "internal",
            "status": "configured",
        },
        {
            "provider": "presentation",
            "label": "展示稿服务",
            "mode": settings.presentation_mode,
            "status": "mock"
            if settings.presentation_mode == "mock"
            else "configured"
            if settings.presentation_service_url
            else "not_configured",
        },
        {
            "provider": "interactive-html",
            "label": "互动 HTML 生成",
            "mode": settings.interactive_html_mode,
            "status": "mock"
            if settings.interactive_html_mode == "mock"
            else "configured"
            if settings.interactive_html_api_key
            else "not_configured",
        },
        {
            "provider": "worker",
            "label": "持久化任务 Worker",
            "mode": "internal",
            "status": "configured" if settings.database_url else "not_configured",
        },
        {
            "provider": "feishu",
            "label": "飞书开放平台",
            "mode": settings.feishu_mode,
            "status": "mock"
            if settings.feishu_mode == "mock"
            else "configured"
            if settings.feishu_app_id and settings.feishu_app_secret
            else "not_configured",
        },
    ]


@connections_router.get("")
async def list_model_connections(request: Request, current_user: CurrentUser) -> dict:
    return success_response(request, {"items": _connections()})


@connections_router.post("/{provider}/test")
async def test_model_connection(
    provider: str,
    request: Request,
    session: DatabaseSession,
    current_user: CurrentUser,
    ai_engine: AIEngineDependency,
    _: str = Depends(require_idempotency_key),
) -> dict:
    known = {row["provider"]: row for row in _connections()}
    if provider not in known:
        raise AppError(ErrorCode.VALIDATION_FAILED, "未知服务连接", status_code=404)
    started = time.perf_counter()
    mode = known[provider]["mode"]
    try:
        if provider == "ai":
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
        elif provider == "worker":
            await session.execute(text("SELECT 1"))
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
                    headers={
                        "Authorization": f"Bearer {settings.interactive_html_api_key}"
                    },
                )
                response.raise_for_status()
        elif provider == "deep-research":
            await session.execute(text("SELECT 1"))
        elif mode == "mock":
            pass
        else:
            raise RuntimeError("provider is not configured")
    except Exception as exc:
        raise AppError(
            ErrorCode.MODEL_CONNECTION_UNAVAILABLE,
            "服务连接测试失败",
            status_code=503,
            retryable=True,
            details={"provider": provider, "reason": str(exc)[:300]},
        ) from exc
    return success_response(
        request,
        {
            "provider": provider,
            "status": "mock" if mode == "mock" else "ok",
            "mode": mode,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "tested_at": __import__("datetime")
            .datetime.now(__import__("datetime").UTC)
            .isoformat(),
        },
    )
