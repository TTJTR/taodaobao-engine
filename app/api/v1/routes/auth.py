import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from threading import Lock

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    AuthServiceDependency,
    CurrentUser,
    FeishuAdapterDependency,
    InvitationRedemptionStoreDependency,
    SessionCodecDependency,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.invitations import InvitationClaims, verify_invitation_token
from app.core.responses import success_response

router = APIRouter(route_class=IdempotencyRoute)

_MAX_CONSUMED_INVITATION_PROOFS = 10_000
_consumed_invitation_proofs: dict[str, int] = {}
_invitation_proof_lock = Lock()


class VerifyInvitationRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=256)


def _invitation_signing_key() -> bytes:
    code = (settings.invitation_signing_secret or settings.invitation_code or "").encode()
    return hmac.new(settings.session_secret.encode(), code, hashlib.sha256).digest()


def create_invitation_proof(token_id_hash: str | None = None) -> str:
    expires_at = int(time.time()) + settings.invitation_ttl_seconds
    parts = [str(expires_at), secrets.token_urlsafe(18)]
    if token_id_hash:
        parts.append(token_id_hash)
    payload = ".".join(parts)
    signature = hmac.new(
        _invitation_signing_key(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_invitation_proof(proof: str | None) -> bool:
    if not proof:
        return False
    try:
        parts = proof.split(".")
        if len(parts) not in {3, 4}:
            return False
        expires_at_raw, nonce, *remainder = parts
        signature = remainder[-1]
        expires_at = int(expires_at_raw)
    except (TypeError, ValueError):
        return False
    if not nonce or expires_at < int(time.time()):
        return False
    payload = ".".join(parts[:-1])
    expected = hmac.new(
        _invitation_signing_key(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def invitation_proof_token_hash(proof: str | None) -> str | None:
    if not verify_invitation_proof(proof) or proof is None:
        return None
    parts = proof.split(".")
    return parts[2] if len(parts) == 4 else None


def consume_invitation_proof(proof: str | None) -> bool:
    """Atomically accept a valid invitation proof no more than once per process."""
    if not verify_invitation_proof(proof):
        return False
    assert proof is not None
    expires_at = int(proof.split(".", maxsplit=1)[0])
    fingerprint = hashlib.sha256(proof.encode()).hexdigest()
    now = int(time.time())
    with _invitation_proof_lock:
        expired = [
            item
            for item, item_expires_at in _consumed_invitation_proofs.items()
            if item_expires_at < now
        ]
        for item in expired:
            del _consumed_invitation_proofs[item]
        if fingerprint in _consumed_invitation_proofs:
            return False
        if len(_consumed_invitation_proofs) >= _MAX_CONSUMED_INVITATION_PROOFS:
            return False
        _consumed_invitation_proofs[fingerprint] = expires_at
    return True


@router.post("/auth/invitation/verify")
async def verify_invitation(
    payload: VerifyInvitationRequest,
    request: Request,
    response: Response,
    redemption_store: InvitationRedemptionStoreDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    claims: InvitationClaims | None = None
    if settings.invitation_signing_secret:
        claims = verify_invitation_token(
            payload.invitation_code,
            settings.invitation_signing_secret,
            max_ttl_seconds=settings.invitation_max_token_ttl_seconds,
        )
        if claims is not None:
            expires_at = datetime.fromtimestamp(claims.expires_at, tz=UTC)
            if not await redemption_store.redeem(claims.token_id_hash, expires_at):
                claims = None
    else:
        configured = settings.invitation_code or ""
        if configured and hmac.compare_digest(configured, payload.invitation_code):
            claims = InvitationClaims("legacy", int(time.time()), int(time.time()) + 60)
    if claims is None:
        raise AppError(
            ErrorCode.INVITE_CODE_INVALID,
            "邀请码无效",
            status_code=401,
        )
    response.set_cookie(
        settings.invitation_cookie_name,
        create_invitation_proof(None if claims.token_id == "legacy" else claims.token_id_hash),
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
    redemption_store: InvitationRedemptionStoreDependency,
    invitation: str | None = Cookie(default=None, alias=settings.invitation_cookie_name),
) -> JSONResponse:
    if settings.invitation_required:
        token_hash = invitation_proof_token_hash(invitation)
        accepted = (
            await redemption_store.consume(token_hash)
            if token_hash
            else consume_invitation_proof(invitation)
        )
        if not accepted:
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
