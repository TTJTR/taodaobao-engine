from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode
from app.core.idempotency import IDEMPOTENT_METHODS
from app.core.token_crypto import EncryptedTokenText
from app.db.models import WorkspaceModelConnection
from app.schemas.model_connections import ConfigureModelConnectionRequest
from app.services import model_connection_service as service


def test_catalog_only_exposes_allowlisted_live_providers() -> None:
    catalog = service.catalog_payload()

    assert {item["capability"] for item in catalog} == {"ai", "interactive-html"}
    assert {provider["provider"] for item in catalog for provider in item["providers"]} == {
        "dashscope",
        "deepseek",
    }
    assert "base_url" not in str(catalog)


def test_all_model_connection_writes_are_idempotent() -> None:
    assert {"PUT", "DELETE"} <= IDEMPOTENT_METHODS


def test_connection_request_rejects_unapproved_provider_and_model_name() -> None:
    with pytest.raises(ValidationError):
        ConfigureModelConnectionRequest(
            provider="custom",
            model="model",
            api_key="secret-key",
        )
    with pytest.raises(ValidationError):
        ConfigureModelConnectionRequest(
            provider="deepseek",
            model="model name with spaces",
            api_key="secret-key",
        )


def test_api_key_column_uses_encryption_type() -> None:
    column = WorkspaceModelConnection.__table__.c.api_key

    assert isinstance(column.type, EncryptedTokenText)


@pytest.mark.asyncio
async def test_candidate_must_return_real_expected_json(monkeypatch) -> None:
    client = AsyncMock()
    client.generate_json.return_value = {"status": "not-ok"}
    monkeypatch.setattr(service, "BailianChatClient", lambda _: client)

    with pytest.raises(AppError) as captured:
        await service.test_candidate("deepseek", "deepseek-chat", "secret-key")

    assert captured.value.code == ErrorCode.MODEL_CONNECTION_UNAVAILABLE
    client.generate_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_workspace_ai_engine_uses_selected_provider_without_mock(monkeypatch) -> None:
    row = WorkspaceModelConnection(
        workspace_id=uuid4(),
        capability="ai",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="secret-key",
        updated_by_id=uuid4(),
        last_test_status="ok",
        last_test_at=service.datetime.now(service.UTC),
    )
    session = AsyncMock()
    session.scalar.return_value = row

    engine = await service.workspace_ai_engine(session, row.workspace_id)

    settings = engine._model_client._settings
    assert settings.base_url == "https://api.deepseek.com"
    assert settings.chat_model == "deepseek-chat"
    assert settings.api_key == "secret-key"


def test_frontend_console_never_stores_api_key() -> None:
    frontend = open("static/index.html", encoding="utf-8").read()

    assert "getModelConnectionCatalog" in frontend
    assert "configureModelConnection" in frontend
    assert "真实测试并保存" in frontend
    assert 'type="password"' in frontend
    assert "API Key 必须完整填写" in frontend
    assert "apiKey:" not in frontend
