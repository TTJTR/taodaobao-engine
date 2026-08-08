import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

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


class VerifyInvitationRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=256)


def _invitation_signing_key() -> bytes:
    code = (settings.invitation_code or "").encode()
    return hmac.new(settings.session_secret.encode(), code, hashlib.sha256).digest()


def create_invitation_proof() -> str:
    expires_at = int(time.time()) + settings.invitation_ttl_seconds
    payload = f"{expires_at}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(
        _invitation_signing_key(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_invitation_proof(proof: str | None) -> bool:
    if not proof:
        return False
    try:
        expires_at_raw, nonce, signature = proof.split(".", maxsplit=2)
        expires_at = int(expires_at_raw)
    except (TypeError, ValueError):
        return False
    if not nonce or expires_at < int(time.time()):
        return False
    payload = f"{expires_at_raw}.{nonce}"
    expected = hmac.new(
        _invitation_signing_key(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/auth/invitation/verify")
async def verify_invitation(
    payload: VerifyInvitationRequest,
    request: Request,
    response: Response,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    configured = settings.invitation_code or ""
    if not configured or not hmac.compare_digest(configured, payload.invitation_code):
        raise AppError(
            ErrorCode.INVITE_CODE_INVALID,
            "邀请码无效",
            status_code=401,
        )
    response.set_cookie(
        settings.invitation_cookie_name,
        create_invitation_proof(),
        max_age=settings.invitation_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=f"{settings.api_prefix}/auth/feishu/start",
    )
    return success_response(request, {"success": True})


def get_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/auth/feishu/callback"


@router.get("/auth/feishu/start", summary="获取飞书授权地址")
async def start_feishu_authorization(
    request: Request,
    adapter: FeishuAdapterDependency,
    invitation: str | None = Cookie(default=None, alias=settings.invitation_cookie_name),
) -> JSONResponse:
    if settings.invitation_required and not verify_invitation_proof(invitation):
        raise AppError(ErrorCode.INVITE_CODE_INVALID, "请先验证邀请码", status_code=401)
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
    if settings.invitation_required:
        response.delete_cookie(
            settings.invitation_cookie_name,
            path=f"{settings.api_prefix}/auth/feishu/start",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
    return response


@router.get("/auth/feishu/callback", summary="处理飞书 OAuth 回调")
async def handle_feishu_callback(
    code: str,
    state: str,
    service: AuthServiceDependency,
    codec: SessionCodecDependency,
    oauth_state: str | None = Cookie(default=None, alias=settings.oauth_state_cookie_name),
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
