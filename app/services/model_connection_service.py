import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import BailianAIEngine, BailianChatClient, BailianSettings
from app.ai.model_client import BAILIAN_BEIJING_BASE_URL, ModelClientError
from app.ai.rehearsal import RehearsalAIWorkflow
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.token_crypto import get_token_cipher
from app.db.models import WorkspaceModelConnection
from app.integrations.bailian_web_search import BailianWebSearchAdapter
from app.services.interactive_html_service import LiveInteractiveHTMLProvider

PROVIDER_CATALOG = {
    "dashscope": {
        "label": "阿里云百炼",
        "base_url": BAILIAN_BEIJING_BASE_URL,
        "default_models": {
            "ai": "qwen-plus",
            "interactive-html": "qwen-plus",
            "web-search": "qwen-plus",
        },
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_models": {"ai": "deepseek-chat", "interactive-html": "deepseek-chat"},
    },
}
CAPABILITIES = {
    "ai": {"label": "可信 AI 与研究", "providers": ["dashscope", "deepseek"]},
    "interactive-html": {
        "label": "互动 HTML 生成",
        "providers": ["deepseek", "dashscope"],
    },
    "web-search": {
        "label": "公开情报搜索",
        "providers": ["dashscope"],
    },
}


def catalog_payload() -> list[dict]:
    return [
        {
            "capability": capability,
            "label": values["label"],
            "providers": [
                {
                    "provider": provider,
                    "label": PROVIDER_CATALOG[provider]["label"],
                    "default_model": PROVIDER_CATALOG[provider]["default_models"][capability],
                }
                for provider in values["providers"]
            ],
        }
        for capability, values in CAPABILITIES.items()
    ]


async def get_workspace_connection(
    session: AsyncSession, workspace_id: uuid.UUID, capability: str
) -> WorkspaceModelConnection | None:
    return await session.scalar(
        select(WorkspaceModelConnection).where(
            WorkspaceModelConnection.workspace_id == workspace_id,
            WorkspaceModelConnection.capability == capability,
            WorkspaceModelConnection.is_deleted.is_(False),
        )
    )


def mask_api_key(value: str) -> str:
    suffix = value[-4:] if len(value) >= 4 else "****"
    return f"••••••••{suffix}"


def _candidate_settings(provider: str, model: str, api_key: str) -> BailianSettings:
    try:
        config = PROVIDER_CATALOG[provider]
    except KeyError as exc:
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "不支持的模型 Provider",
            status_code=422,
        ) from exc
    return BailianSettings(
        api_key=api_key,
        chat_model=model,
        base_url=str(config["base_url"]),
        # The quick workflow has a 60-second shared deadline. Allow the first
        # structured generation enough time to finish while the outer budget
        # still caps the complete generation-and-verification workflow.
        timeout_seconds=40.0,
        max_retries=0,
        temperature=0.0,
    )


async def test_candidate(
    provider: str,
    model: str,
    api_key: str,
    capability: str = "ai",
) -> int:
    started = time.perf_counter()
    try:
        if capability == "web-search":
            if provider != "dashscope":
                raise ValueError("web search only supports dashscope")
            adapter = BailianWebSearchAdapter(
                api_key,
                model=model,
                base_url=settings.bailian_search_base_url,
                strategy=settings.bailian_search_strategy,
                timeout_seconds=settings.bailian_search_timeout_seconds,
            )
            try:
                await adapter.search("阿里云官网", max_results=1)
            finally:
                await adapter.aclose()
            return round((time.perf_counter() - started) * 1000)
        client = BailianChatClient(_candidate_settings(provider, model, api_key))
        result = await client.generate_json(
            "你是 API 连通性检查器，只返回 JSON 对象。",
            '返回 {"status":"ok"}，不要添加其他字段。',
        )
        if result.get("status") != "ok":
            raise ModelClientError("provider returned an unexpected connection-test payload")
    except Exception as exc:
        raise AppError(
            ErrorCode.MODEL_CONNECTION_UNAVAILABLE,
            "API 连接、模型权限或 JSON 输出检查失败",
            status_code=503,
            retryable=True,
            details={"provider": provider, "model": model, "reason": type(exc).__name__},
        ) from exc
    return round((time.perf_counter() - started) * 1000)


async def configure_workspace_connection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    capability: str,
    provider: str,
    model: str,
    api_key: str,
) -> WorkspaceModelConnection:
    if capability not in CAPABILITIES or provider not in CAPABILITIES[capability]["providers"]:
        raise AppError(
            ErrorCode.VALIDATION_FAILED,
            "该能力不支持所选 Provider",
            status_code=422,
        )
    if get_token_cipher() is None:
        raise AppError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "服务端未配置密钥加密能力，拒绝保存 API Key",
            status_code=503,
        )
    latency_ms = await test_candidate(provider, model, api_key, capability)
    row = await get_workspace_connection(session, workspace_id, capability)
    now = datetime.now(UTC)
    if row is None:
        row = WorkspaceModelConnection(
            workspace_id=workspace_id,
            capability=capability,
            provider=provider,
            base_url=str(PROVIDER_CATALOG[provider]["base_url"]),
            model=model,
            api_key=api_key,
            updated_by_id=user_id,
            last_test_status="ok",
            last_test_at=now,
            last_latency_ms=latency_ms,
        )
        session.add(row)
    else:
        row.provider = provider
        row.base_url = str(PROVIDER_CATALOG[provider]["base_url"])
        row.model = model
        row.api_key = api_key
        row.updated_by_id = user_id
        row.last_test_status = "ok"
        row.last_test_at = now
        row.last_latency_ms = latency_ms
    await session.commit()
    await session.refresh(row)
    return row


async def delete_workspace_connection(
    session: AsyncSession, workspace_id: uuid.UUID, capability: str
) -> bool:
    row = await get_workspace_connection(session, workspace_id, capability)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


def connection_payload(row: WorkspaceModelConnection) -> dict:
    return {
        "capability": row.capability,
        "provider": row.capability,
        "selected_provider": row.provider,
        "provider_label": PROVIDER_CATALOG[row.provider]["label"],
        "model": row.model,
        "configured": True,
        "masked_value": mask_api_key(row.api_key),
        "source": "workspace",
        "updated_at": row.updated_at.isoformat(),
        "last_test_status": row.last_test_status,
        "last_test_at": row.last_test_at.isoformat(),
        "latency_ms": row.last_latency_ms,
    }


def _default_ai_settings() -> BailianSettings:
    return BailianSettings.from_env(Path(".env"))


async def workspace_ai_engine(session: AsyncSession, workspace_id: uuid.UUID) -> BailianAIEngine:
    row = await get_workspace_connection(session, workspace_id, "ai")
    selected = (
        _candidate_settings(row.provider, row.model, row.api_key) if row else _default_ai_settings()
    )
    return BailianAIEngine(BailianChatClient(selected))


async def workspace_rehearsal_workflow(
    session: AsyncSession, workspace_id: uuid.UUID
) -> RehearsalAIWorkflow:
    row = await get_workspace_connection(session, workspace_id, "ai")
    selected = (
        _candidate_settings(row.provider, row.model, row.api_key) if row else _default_ai_settings()
    )
    return RehearsalAIWorkflow(
        BailianChatClient(selected),
        timeout_seconds=settings.rehearsal_ai_timeout_seconds,
        max_prompt_characters=settings.rehearsal_ai_max_prompt_characters,
    )


async def workspace_interactive_provider(
    session: AsyncSession, workspace_id: uuid.UUID
) -> LiveInteractiveHTMLProvider | None:
    row = await get_workspace_connection(session, workspace_id, "interactive-html")
    if row:
        return LiveInteractiveHTMLProvider(
            row.base_url,
            row.api_key,
            row.model,
            settings.interactive_html_timeout_seconds,
        )
    if settings.interactive_html_mode == "live" and settings.interactive_html_api_key:
        return LiveInteractiveHTMLProvider(
            settings.interactive_html_base_url,
            settings.interactive_html_api_key,
            settings.interactive_html_model,
            settings.interactive_html_timeout_seconds,
        )
    return None


def default_bailian_search_api_key() -> str | None:
    return (settings.bailian_search_api_key or os.getenv("DASHSCOPE_API_KEY") or "").strip() or None


def bailian_search_is_configured() -> bool:
    return settings.enable_public_intelligence and default_bailian_search_api_key() is not None


async def workspace_search_provider(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> BailianWebSearchAdapter | None:
    if not settings.enable_public_intelligence:
        return None
    row = await get_workspace_connection(session, workspace_id, "web-search")
    if row is not None:
        return BailianWebSearchAdapter(
            row.api_key,
            model=row.model,
            base_url=settings.bailian_search_base_url,
            strategy=settings.bailian_search_strategy,
            timeout_seconds=settings.bailian_search_timeout_seconds,
        )
    api_key = default_bailian_search_api_key()
    if api_key is None:
        return None
    return BailianWebSearchAdapter(
        api_key,
        model=settings.bailian_search_model,
        base_url=settings.bailian_search_base_url,
        strategy=settings.bailian_search_strategy,
        timeout_seconds=settings.bailian_search_timeout_seconds,
    )
