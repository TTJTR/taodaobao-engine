import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import (
    BailianAIEngine,
    BailianChatClient,
    BailianSettings,
)
from app.ai.embedding import (
    BailianEmbeddingProvider,
    BailianEmbeddingSettings,
    EmbeddingProvider,
)
from app.ai.model_client import ModelClientError
from app.ai.rehearsal import RehearsalAIWorkflow
from app.contracts.ai import AIEngine
from app.contracts.presentation import SlidePlanner
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import InvalidSessionError, SessionCodec
from app.core.token_crypto import validate_live_token_encryption
from app.db.database import get_db
from app.db.models import User
from app.db.repositories import UserRepository
from app.integrations import FeishuAdapter, LiveFeishuAdapter
from app.integrations.presentation_planner import AIEngineSlidePlanner
from app.services.auth import AuthService
from app.services.invitations import (
    InvitationRedemptionStore,
    PostgresInvitationRedemptionStore,
)
from app.services.model_connection_service import (
    workspace_ai_engine,
    workspace_rehearsal_workflow,
)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@lru_cache
def get_session_codec() -> SessionCodec:
    return SessionCodec(settings.session_secret, settings.session_ttl_seconds)


@lru_cache
def get_feishu_adapter() -> FeishuAdapter:
    if settings.feishu_mode != "live":
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "飞书服务未启用",
            status_code=503,
            retryable=False,
        )
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "飞书服务未配置",
            status_code=503,
            retryable=False,
        )
    validate_live_token_encryption()
    return LiveFeishuAdapter(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        scopes=settings.feishu_scopes.split(),
    )


SessionCodecDependency = Annotated[SessionCodec, Depends(get_session_codec)]
FeishuAdapterDependency = Annotated[FeishuAdapter, Depends(get_feishu_adapter)]


@lru_cache
def get_invitation_redemption_store() -> InvitationRedemptionStore:
    return PostgresInvitationRedemptionStore()


InvitationRedemptionStoreDependency = Annotated[
    InvitationRedemptionStore, Depends(get_invitation_redemption_store)
]


@lru_cache
def get_ai_engine() -> AIEngine:
    if settings.ai_mode != "live":
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "AI 服务未启用",
            status_code=503,
            retryable=False,
        )
    try:
        bailian_settings = BailianSettings.from_env(Path(".env"))
    except (ModelClientError, ValueError) as exc:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "AI 服务未配置",
            status_code=503,
            retryable=False,
        ) from exc
    return BailianAIEngine(BailianChatClient(bailian_settings))


@lru_cache
def get_rehearsal_ai_workflow() -> RehearsalAIWorkflow:
    if settings.ai_mode != "live":
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "AI 演练服务未启用",
            status_code=503,
            retryable=False,
        )
    try:
        bailian_settings = BailianSettings.from_env(Path(".env"))
    except (ModelClientError, ValueError) as exc:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "AI 演练服务未配置",
            status_code=503,
            retryable=False,
        ) from exc
    return RehearsalAIWorkflow(
        BailianChatClient(bailian_settings),
        timeout_seconds=settings.rehearsal_ai_timeout_seconds,
        max_prompt_characters=settings.rehearsal_ai_max_prompt_characters,
    )


@lru_cache
def get_slide_planner() -> SlidePlanner:
    if settings.ai_mode != "live":
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "AI 展示规划服务未启用",
            status_code=503,
            retryable=False,
        )
    return AIEngineSlidePlanner(get_ai_engine())


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    if settings.ai_mode != "live":
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Embedding 服务未启用",
            status_code=503,
            retryable=False,
        )
    try:
        provider_settings = BailianEmbeddingSettings.from_env(Path(".env"))
    except (ModelClientError, ValueError) as exc:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Embedding 服务未配置",
            status_code=503,
            retryable=False,
        ) from exc
    return BailianEmbeddingProvider(provider_settings)


def bailian_is_configured() -> bool:
    try:
        BailianSettings.from_env(Path(".env"))
        BailianEmbeddingSettings.from_env(Path(".env"))
    except (ModelClientError, ValueError):
        return False
    return True


EmbeddingProviderDependency = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


async def get_current_user(
    session: DatabaseSession,
    codec: SessionCodecDependency,
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
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
    if (
        user.feishu_refresh_token
        and user.feishu_token_expires_at
        and user.feishu_token_expires_at <= datetime.now(UTC) + timedelta(seconds=60)
    ):
        # Resolve Feishu only after a valid session has been established. This
        # preserves a truthful 401 for anonymous requests even when Feishu is
        # intentionally unconfigured in an isolated test or offline runtime.
        adapter = get_feishu_adapter()
        try:
            token = await adapter.refresh_access_token(user.feishu_refresh_token)
        except Exception as exc:
            raise AppError(
                ErrorCode.FEISHU_AUTH_EXPIRED,
                "飞书授权已过期，请重新授权",
                status_code=401,
            ) from exc
        user.feishu_access_token = token.access_token
        user.feishu_refresh_token = token.refresh_token
        user.feishu_token_expires_at = token.expires_at
        await session.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_workspace_id(current_user: CurrentUser) -> uuid.UUID:
    return current_user.workspace_id


WorkspaceId = Annotated[uuid.UUID, Depends(get_workspace_id)]


async def get_workspace_ai_engine(session: DatabaseSession, workspace_id: WorkspaceId) -> AIEngine:
    try:
        return await workspace_ai_engine(session, workspace_id)
    except (ModelClientError, ValueError) as exc:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "当前工作区的 AI API 未配置或不可用",
            status_code=503,
            retryable=False,
        ) from exc


async def get_workspace_rehearsal_ai_workflow(
    session: DatabaseSession, workspace_id: WorkspaceId
) -> RehearsalAIWorkflow:
    try:
        return await workspace_rehearsal_workflow(session, workspace_id)
    except (ModelClientError, ValueError) as exc:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "当前工作区的 AI API 未配置或不可用",
            status_code=503,
            retryable=False,
        ) from exc


async def get_workspace_slide_planner(
    ai_engine: Annotated[AIEngine, Depends(get_workspace_ai_engine)],
) -> SlidePlanner:
    return AIEngineSlidePlanner(ai_engine)


AIEngineDependency = Annotated[AIEngine, Depends(get_workspace_ai_engine)]
RehearsalAIWorkflowDependency = Annotated[
    RehearsalAIWorkflow, Depends(get_workspace_rehearsal_ai_workflow)
]
SlidePlannerDependency = Annotated[SlidePlanner, Depends(get_workspace_slide_planner)]


def get_auth_service(
    session: DatabaseSession,
    adapter: FeishuAdapterDependency,
) -> AuthService:
    return AuthService(session, adapter, settings.demo_workspace_id)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
