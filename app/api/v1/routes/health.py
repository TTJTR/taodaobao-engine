import os
from typing import Any

import asyncpg
from fastapi import APIRouter, Request
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.responses import success_response

router = APIRouter()


@router.get("/health", summary="服务健康检查")
async def health_check(request: Request) -> dict[str, Any]:
    database_status = "not_configured"
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
            database_status = "ok"
        except Exception:
            database_status = "unavailable"
        finally:
            if connection is not None:
                await connection.close()

    if settings.ai_mode == "mock":
        ai_status = "mock"
    else:
        ai_status = "ok" if os.getenv("DASHSCOPE_API_KEY") else "not_configured"
    if settings.feishu_mode == "mock":
        feishu_status = "mock"
    else:
        required_feishu_settings = (
            settings.feishu_app_id,
            settings.feishu_app_secret,
            settings.feishu_verification_token,
            settings.feishu_encrypt_key,
            settings.feishu_token_encryption_key,
        )
        feishu_status = "configured" if all(required_feishu_settings) else "not_configured"
    status = (
        "ok"
        if database_status in {"ok", "not_configured"}
        and ai_status in {"ok", "mock"}
        and feishu_status in {"configured", "mock"}
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
        },
    )
