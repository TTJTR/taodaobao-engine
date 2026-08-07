import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.ai import AIEngine
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import InvalidSessionError, SessionCodec
from app.db.database import get_db
from app.db.models import User
from app.db.repositories import UserRepository
from app.integrations import FeishuAdapter, LiveFeishuAdapter, MockAIEngine, MockFeishuAdapter
from app.services.auth import AuthService

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@lru_cache
def get_session_codec() -> SessionCodec:
    return SessionCodec(settings.session_secret, settings.session_ttl_seconds)


@lru_cache
def get_feishu_adapter() -> FeishuAdapter:
    if settings.feishu_mode == "mock":
        return MockFeishuAdapter(settings.public_base_url)
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("Live Feishu mode requires APP_FEISHU_APP_ID and APP_FEISHU_APP_SECRET")
    return LiveFeishuAdapter(settings.feishu_app_id, settings.feishu_app_secret)


SessionCodecDependency = Annotated[SessionCodec, Depends(get_session_codec)]
FeishuAdapterDependency = Annotated[FeishuAdapter, Depends(get_feishu_adapter)]


@lru_cache
def get_ai_engine() -> AIEngine:
    if settings.ai_mode == "mock":
        return MockAIEngine()
    raise RuntimeError("Live AI mode requires a configured AIEngine implementation")


AIEngineDependency = Annotated[AIEngine, Depends(get_ai_engine)]


async def get_current_user(
    session: DatabaseSession,
    codec: SessionCodecDependency,
    session_token: Annotated[
        str | None, Cookie(alias=settings.session_cookie_name)
    ] = None,
) -> User:
    if not session_token:
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "请先登录",
            status_code=401,
        )
    try:
        claims = codec.decode(session_token)
    except InvalidSessionError as exc:
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "登录状态已失效，请重新登录",
            status_code=401,
        ) from exc

    user = await UserRepository(session, claims.workspace_id).get(claims.user_id)
    if user is None:
        raise AppError(
            ErrorCode.AUTH_REQUIRED,
            "登录用户不存在或已停用",
            status_code=401,
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_workspace_id(current_user: CurrentUser) -> uuid.UUID:
    return current_user.workspace_id


WorkspaceId = Annotated[uuid.UUID, Depends(get_workspace_id)]


def get_auth_service(
    session: DatabaseSession,
    adapter: FeishuAdapterDependency,
) -> AuthService:
    return AuthService(session, adapter, settings.demo_workspace_id)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
