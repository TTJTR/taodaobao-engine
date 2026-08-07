import hmac
import secrets

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.deps import (
    AuthServiceDependency,
    CurrentUser,
    FeishuAdapterDependency,
    SessionCodecDependency,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.responses import success_response

router = APIRouter(route_class=IdempotencyRoute)


def get_redirect_uri() -> str:
    return (
        f"{settings.public_base_url.rstrip('/')}"
        f"{settings.api_prefix}/auth/feishu/callback"
    )


@router.get("/auth/feishu/start", summary="获取飞书授权地址")
async def start_feishu_authorization(
    request: Request,
    adapter: FeishuAdapterDependency,
) -> JSONResponse:
    state = secrets.token_urlsafe(32)
    authorization_url = adapter.get_authorization_url(state, get_redirect_uri())
    response = JSONResponse(
        content=success_response(
            request,
            {"authorization_url": authorization_url, "state": state},
        )
    )
    response.set_cookie(
        settings.oauth_state_cookie_name,
        state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=f"{settings.api_prefix}/auth/feishu/callback",
    )
    return response


@router.get("/auth/feishu/callback", summary="处理飞书 OAuth 回调")
async def handle_feishu_callback(
    code: str,
    state: str,
    service: AuthServiceDependency,
    codec: SessionCodecDependency,
    oauth_state: str | None = Cookie(
        default=None, alias=settings.oauth_state_cookie_name
    ),
) -> RedirectResponse:
    if not oauth_state or not hmac.compare_digest(oauth_state, state):
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "飞书授权 state 无效",
            status_code=400,
        )

    user = await service.handle_feishu_callback(code, get_redirect_uri())
    session_token = codec.encode(user.id, user.workspace_id)
    response = RedirectResponse(settings.frontend_redirect_url, status_code=302)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        settings.oauth_state_cookie_name,
        path=f"{settings.api_prefix}/auth/feishu/callback",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me", summary="获取当前用户与工作空间")
async def get_me(request: Request, current_user: CurrentUser) -> dict[str, object]:
    return success_response(
        request,
        {
            "id": str(current_user.id),
            "name": current_user.name,
            "avatar": current_user.avatar,
            "workspace_id": str(current_user.workspace_id),
        },
    )


@router.post("/auth/logout", summary="退出登录")
async def logout(
    request: Request,
    response: Response,
    current_user: CurrentUser,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    del current_user
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return success_response(request, {"success": True})
