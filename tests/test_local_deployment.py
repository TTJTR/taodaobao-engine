import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.embedding import BailianEmbeddingSettings
from app.ai.model_client import BailianSettings
from app.api.v1.routes import auth, sources
from app.core.config import Settings, settings
from app.core.logging import redact_secrets
from app.core.security import SessionCodec
from app.main import create_app


def test_local_production_settings_validate_listening_and_secrets() -> None:
    configured = Settings(
        _env_file=None,
        env="production",
        host="127.0.0.1",
        auth_mode="local",
        local_admin_username="admin",
        local_admin_password="a-safe-local-password",
        session_secret="session-secret-with-at-least-thirty-two-characters",
        secret_encryption_key=Fernet.generate_key().decode(),
    )

    assert configured.parsed_cors_origins == [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


@pytest.mark.parametrize("host", ["localhost", "192.168.1.10", "::"])
def test_settings_reject_implicit_or_ambiguous_bind_addresses(host: str) -> None:
    with pytest.raises(ValidationError, match="APP_HOST"):
        Settings(_env_file=None, host=host)


def test_generic_openai_schema_environment_has_priority(monkeypatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "generic-key")
    monkeypatch.setenv("AI_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("CHAT_MODEL", "chat-local")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embeddings.example.test/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-local")

    chat = BailianSettings.from_env()
    embedding = BailianEmbeddingSettings.from_env()

    assert chat.api_key == "generic-key"
    assert chat.base_url == "https://models.example.test/v1"
    assert chat.chat_model == "chat-local"
    assert embedding.api_key == "embedding-key"
    assert embedding.base_url == "https://embeddings.example.test/v1"
    assert embedding.model == "embedding-local"


def test_log_redaction_covers_credentials_and_oauth_query_values() -> None:
    message = (
        "Authorization: Bearer secret-token password=hunter2 "
        "callback?code=oauth-code&state=oauth-state"
    )
    redacted = redact_secrets(message)

    for secret in ("secret-token", "hunter2", "oauth-code", "oauth-state"):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_auth_modes_never_expose_local_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "enable_feishu", False)
    monkeypatch.setattr(settings, "enable_demo_data", False)
    payload = TestClient(create_app()).get("/api/v1/auth/modes").json()

    assert payload["data"] == {"local": True, "feishu": False, "demo_data": False}
    assert "password" not in str(payload).lower()


@pytest.mark.asyncio
async def test_local_login_creates_http_only_session_without_feishu(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), workspace_id=workspace_id)

    class LocalUsers:
        def __init__(self, session, selected_workspace_id) -> None:
            assert selected_workspace_id == workspace_id

        async def get_by_feishu_user_id(self, identity: str):
            assert identity.startswith("local:")
            return user

    monkeypatch.setattr(auth, "UserRepository", LocalUsers)
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "local_admin_username", "admin")
    monkeypatch.setattr(settings, "local_admin_password", "a-safe-local-password")
    monkeypatch.setattr(settings, "local_workspace_id", workspace_id)
    monkeypatch.setattr(settings, "cookie_secure", False)
    response = Response()

    result = await auth.local_login(
        auth.LocalLoginRequest(username="admin", password="a-safe-local-password"),
        SimpleNamespace(state=SimpleNamespace(request_id="req-local")),
        response,
        AsyncMock(),
        SessionCodec("session-secret-at-least-32-characters", 60),
        "idempotency-key",
    )

    assert result["data"]["access_mode"] == "local"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "a-safe-local-password" not in response.headers["set-cookie"]


def test_text_import_route_has_no_feishu_adapter_dependency() -> None:
    parameters = inspect.signature(sources.import_text).parameters

    assert "adapter" not in parameters
