from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from app.api.deps import DatabaseSession
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.responses import success_response
from app.services.usage_monitor_service import UsageMonitorService

router = APIRouter()
DEFAULT_MONITOR_TOKEN_FILE = Path("/run/secrets/taodaobao_usage_monitor_token")


def configured_monitor_token() -> str:
    configured = (settings.usage_monitor_token or "").strip()
    if configured:
        return configured
    try:
        return DEFAULT_MONITOR_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def require_monitor_token(value: str | None) -> None:
    expected = configured_monitor_token()
    if not expected:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "API 消耗监控尚未配置",
            status_code=503,
            retryable=False,
        )
    if value is None or not secrets.compare_digest(value, expected):
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "监控凭证无效",
            status_code=401,
            retryable=False,
        )


@router.get("")
async def api_usage_summary(
    request: Request,
    session: DatabaseSession,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    x_monitor_token: Annotated[str | None, Header(alias="X-Monitor-Token")] = None,
) -> dict:
    require_monitor_token(x_monitor_token)
    summary = await UsageMonitorService(session).summary(hours)
    return success_response(request, summary)
