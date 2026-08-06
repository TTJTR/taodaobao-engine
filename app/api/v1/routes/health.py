from typing import Any

from fastapi import APIRouter, Request

from app.core.responses import success_response

router = APIRouter()


@router.get("/health", summary="服务健康检查")
async def health_check(request: Request) -> dict[str, Any]:
    return success_response(
        request,
        {
            "status": "ok",
            "service": "ok",
            "database": "not_configured",
            "ai": "mock",
            "ai_mode": "mock",
        },
    )
