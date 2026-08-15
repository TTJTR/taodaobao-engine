from typing import Any

import asyncpg
import httpx
from fastapi import APIRouter, Request
from sqlalchemy.engine import make_url

from app.api.deps import bailian_is_configured
from app.core.config import settings
from app.core.responses import success_response
from app.services.model_connection_service import bailian_search_is_configured

router = APIRouter()


def _sidecar_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(2.0, connect=1.0),
        follow_redirects=False,
        trust_env=False,
    )


async def probe_open_enrich_sidecar() -> str:
    if not settings.open_enrich_svc_url:
        return "not_configured"
    try:
        async with _sidecar_client() as client:
            response = await client.get(
                f"{settings.open_enrich_svc_url.rstrip('/')}/healthz"
            )
            if response.status_code != 200:
                return "unreachable"
            payload = response.json()
            return "ok" if payload == {"status": "ok"} else "unreachable"
    except (httpx.HTTPError, ValueError):
        return "unreachable"


@router.get("/health", summary="服务健康检查")
async def health_check(request: Request) -> dict[str, Any]:
    database_status = "not_configured"
    sidecar_status = await probe_open_enrich_sidecar()
    workflow_queue: dict[str, int] | None = None
    if settings.database_url:
        connection = None
        try:
            url = make_url(settings.database_url)
            connection = await asyncpg.connect(
                host=url.host,
                port=url.port or 5432,
                user=url.username,
                password=url.password,
                database=url.database,
                timeout=2,
            )
            await connection.fetchval("SELECT 1")
            workflow_queue = {
                row["status"]: int(row["count"])
                for row in await connection.fetch(
                    "SELECT status, count(*) AS count FROM workflow_tasks "
                    "WHERE is_deleted = false GROUP BY status"
                )
            }
            workflow_queue["stale_leases"] = int(
                await connection.fetchval(
                    "SELECT count(*) FROM workflow_tasks WHERE is_deleted = false "
                    "AND status = 'running' AND lease_expires_at < now()"
                )
                or 0
            )
            database_status = "ok"
        except Exception:
            database_status = "unavailable"
        finally:
            if connection is not None:
                await connection.close()

    ai_status = (
        "ok"
        if settings.ai_mode == "live" and bailian_is_configured()
        else "not_configured"
    )
    if settings.feishu_mode == "live":
        required_feishu_settings = (
            settings.feishu_app_id,
            settings.feishu_app_secret,
            settings.feishu_verification_token,
            settings.feishu_encrypt_key,
            settings.feishu_token_encryption_key,
        )
        feishu_status = "configured" if all(required_feishu_settings) else "not_configured"
    else:
        feishu_status = "not_configured"
    presentation_status = (
        "configured"
        if settings.presentation_mode == "live" and settings.presentation_service_url
        else "not_configured"
    )
    interactive_html_status = (
        "configured"
        if settings.interactive_html_mode == "live" and settings.interactive_html_api_key
        else "not_configured"
    )
    web_search_status = "configured" if bailian_search_is_configured() else "not_configured"
    status = (
        "ok"
        if database_status in {"ok", "not_configured"}
        and ai_status == "ok"
        and feishu_status == "configured"
        and interactive_html_status == "configured"
        else "degraded"
    )
    return success_response(
        request,
        {
            "status": status,
            "service": "ok",
            "database": database_status,
            "ai": ai_status,
            "ai_mode": settings.ai_mode,
            "feishu": feishu_status,
            "feishu_mode": settings.feishu_mode,
            "presentation": presentation_status,
            "presentation_mode": settings.presentation_mode,
            "interactive_html": interactive_html_status,
            "interactive_html_mode": settings.interactive_html_mode,
            "web_search": web_search_status,
            "sidecar_status": sidecar_status,
            "workflow_queue": workflow_queue,
        },
    )
