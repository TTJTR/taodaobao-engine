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
    DatabaseSession,
    FeishuAdapterDependency,
    InvitationRedemptionStoreDependency,
    SessionCodecDependency,
)
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IdempotencyRoute, require_idempotency_key
from app.core.invitations import (
    InvitationClaims,
    hash_short_invitation_code,
    verify_invitation_token,
)
from app.core.responses import success_response
from app.core.security import InvalidSessionError
from app.db.repositories import UserRepository

router = APIRouter(route_class=IdempotencyRoute)

_MAX_CONSUMED_INVITATION_PROOFS = 10_000
_consumed_invitation_proofs: dict[str, int] = {}
_invitation_proof_lock = Lock()


class VerifyInvitationRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=256)


class LocalLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


@router.get("/auth/modes", summary="查询可用登录方式")
async def get_auth_modes(request: Request) -> dict[str, object]:
    local_enabled = settings.auth_mode in {"local", "both"}
    feishu_enabled = (
        settings.auth_mode in {"feishu", "both"}
        and settings.enable_feishu
        and bool(settings.feishu_app_id and settings.feishu_app_secret)
    )
    return success_response(
        request,
        {
            "local": local_enabled,
            "feishu": feishu_enabled,
            "demo_data": settings.enable_demo_data,
        },
    )


@router.post("/auth/local/login", summary="使用本地管理员账户登录")
async def local_login(
    payload: LocalLoginRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
    codec: SessionCodecDependency,
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    if settings.auth_mode not in {"local", "both"}:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "本地登录未启用",
            status_code=503,
        )
    configured_username = settings.local_admin_username or ""
    configured_password = settings.local_admin_password or ""
    valid = hmac.compare_digest(payload.username, configured_username) and hmac.compare_digest(
        payload.password, configured_password
    )
    if not valid:
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "用户名或密码不正确",
            status_code=401,
        )

    identity_hash = hashlib.sha256(configured_username.encode()).hexdigest()[:24]
    users = UserRepository(session, settings.local_workspace_id)
    user = await users.get_by_feishu_user_id(f"local:{identity_hash}")
    if user is None:
        user = await users.create(
            feishu_user_id=f"local:{identity_hash}",
            name=configured_username,
            avatar=None,
        )
        await session.commit()
        await session.refresh(user)

    response.set_cookie(
        settings.session_cookie_name,
        codec.encode(user.id, user.workspace_id),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return success_response(
        request,
        {"success": True, "access_mode": "local", "requires_restart": False},
    )


@router.get("/auth/invitation/status", summary="查询邀请码门禁状态")
async def get_invitation_status(request: Request) -> dict[str, object]:
    return success_response(request, {"required": settings.invitation_required})


def _invitation_signing_key() -> bytes:
    code = (settings.invitation_signing_secret or settings.invitation_code or "").encode()
    return hmac.new(settings.session_secret.encode(), code, hashlib.sha256).digest()


def create_invitation_proof(
    token_id_hash: str | None = None,
    redemption_number: int | None = None,
) -> str:
    expires_at = int(time.time()) + settings.invitation_ttl_seconds
    parts = [str(expires_at), secrets.token_urlsafe(18)]
    if token_id_hash:
        parts.append(token_id_hash)
        parts.append(str(redemption_number or 1))
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
        if len(parts) not in {3, 4, 5}:
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
    return parts[2] if len(parts) in {4, 5} else None


def invitation_proof_redemption_number(proof: str | None) -> int | None:
    if not verify_invitation_proof(proof) or proof is None:
        return None
    parts = proof.split(".")
    if len(parts) == 4:
        return 1
    if len(parts) != 5:
        return None
    try:
        number = int(parts[3])
    except ValueError:
        return None
    return number if number > 0 else None


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
    redemption_number: int | None = None
    proof_token_hash: str | None = None
    if settings.invitation_signing_secret:
        short_code_hash = hash_short_invitation_code(
            payload.invitation_code,
            settings.invitation_signing_secret,
        )
        if short_code_hash is not None:
            registered = await redemption_store.redeem_registered(short_code_hash)
            if registered is not None:
                proof_token_hash = short_code_hash
                redemption_number = registered.redemption_number
        else:
            claims = verify_invitation_token(
                payload.invitation_code,
                settings.invitation_signing_secret,
                max_ttl_seconds=settings.invitation_max_token_ttl_seconds,
            )
        if claims is not None and proof_token_hash is None:
            expires_at = datetime.fromtimestamp(claims.expires_at, tz=UTC)
            redemption_number = await redemption_store.redeem(
                claims.token_id_hash, expires_at, claims.max_uses
            )
            if redemption_number is not None:
                proof_token_hash = claims.token_id_hash
    else:
        configured = settings.invitation_code or ""
        if configured and hmac.compare_digest(configured, payload.invitation_code):
            claims = InvitationClaims("legacy", int(time.time()), int(time.time()) + 60)
    if claims is not None and claims.token_id == "legacy":
        valid_invitation = True
    else:
        valid_invitation = proof_token_hash is not None and redemption_number is not None
    if not valid_invitation:
        raise AppError(
            ErrorCode.INVITE_CODE_INVALID,
            "邀请码无效",
            status_code=401,
        )
    response.set_cookie(
        settings.invitation_cookie_name,
        create_invitation_proof(
            proof_token_hash,
            redemption_number,
        ),
        max_age=settings.invitation_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=f"{settings.api_prefix}/auth",
    )
    return success_response(request, {"success": True})


@router.post("/auth/demo/start", summary="使用已验证邀请码进入共享演示工作区")
async def start_demo_session(
    request: Request,
    response: Response,
    session: DatabaseSession,
    codec: SessionCodecDependency,
    redemption_store: InvitationRedemptionStoreDependency,
    invitation: str | None = Cookie(default=None, alias=settings.invitation_cookie_name),
    _: str = Depends(require_idempotency_key),
) -> dict[str, object]:
    """Create an isolated judge identity inside the shared demo workspace.

    This is deliberately not presented as cross-tenant Feishu OAuth. It only
    grants access to the pre-populated contest workspace after the invitation
    proof has been redeemed and consumed exactly once.
    """
    token_hash = invitation_proof_token_hash(invitation)
    redemption_number = invitation_proof_redemption_number(invitation)
    accepted = (
        await redemption_store.consume(token_hash, redemption_number)
        if token_hash is not None and redemption_number is not None
        else consume_invitation_proof(invitation)
    )
    if not accepted:
        raise AppError(
            ErrorCode.INVITE_CODE_INVALID,
            "邀请码验证已失效，请重新输入",
            status_code=401,
        )

    identity_seed = token_hash or hashlib.sha256((invitation or "").encode()).hexdigest()
    suffix = redemption_number or 1
    demo_identity = f"demo-judge:{identity_seed[:24]}:{suffix}"
    users = UserRepository(session, settings.demo_workspace_id)
    user = await users.get_by_feishu_user_id(demo_identity)
    if user is None:
        user = await users.create(
            feishu_user_id=demo_identity,
            name=f"评委体验用户 {suffix:02d}",
            avatar=None,
        )
        await session.commit()
        await session.refresh(user)

    response.set_cookie(
        settings.session_cookie_name,
        codec.encode(user.id, user.workspace_id),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        settings.invitation_cookie_name,
        path=f"{settings.api_prefix}/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return success_response(
        request,
        {
            "success": True,
            "access_mode": "shared_demo",
            "requires_feishu_oauth": False,
        },
    )


def get_redirect_uri() -> str:
    if settings.feishu_callback_url:
        return settings.feishu_callback_url
    return f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/auth/feishu/callback"


@router.get("/auth/feishu/start", summary="获取飞书授权地址")
async def start_feishu_authorization(
    request: Request,
    adapter: FeishuAdapterDependency,
    codec: SessionCodecDependency,
    redemption_store: InvitationRedemptionStoreDependency,
    invitation: str | None = Cookie(default=None, alias=settings.invitation_cookie_name),
    session_token: str | None = Cookie(default=None, alias=settings.session_cookie_name),
) -> JSONResponse:
    has_valid_session = False
    if session_token:
        try:
            codec.decode(session_token)
            has_valid_session = True
        except InvalidSessionError:
            pass
    if settings.invitation_required and not has_valid_session:
        token_hash = invitation_proof_token_hash(invitation)
        redemption_number = invitation_proof_redemption_number(invitation)
        accepted = (
            await redemption_store.consume(token_hash, redemption_number)
            if token_hash and redemption_number is not None
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
    if settings.invitation_required and not has_valid_session:
        response.delete_cookie(
            settings.invitation_cookie_name,
            path=f"{settings.api_prefix}/auth",
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
