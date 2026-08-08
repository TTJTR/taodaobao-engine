import time
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_auth_service, get_current_user, get_feishu_adapter
from app.api.v1.routes import auth as auth_routes
from app.core.config import Settings, settings
from app.core.security import InvalidSessionError, SessionCodec
from app.db.models import User
from app.integrations import MockFeishuAdapter
from app.main import create_app
from app.services.auth import AuthService


def make_user() -> User:
    user = User(
        workspace_id=settings.demo_workspace_id,
        feishu_user_id="mock_user_001",
        name="演示售前顾问",
        avatar=None,
    )
    user.id = uuid.uuid4()
    return user


def test_session_codec_round_trip_and_tamper_detection() -> None:
    codec = SessionCodec("test-secret-at-least-16-characters", 60)
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    claims = codec.decode(codec.encode(user_id, workspace_id))

    assert claims.user_id == user_id
    assert claims.workspace_id == workspace_id
    with pytest.raises(InvalidSessionError):
        codec.decode(codec.encode(user_id, workspace_id) + "tampered")


def test_session_codec_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    codec = SessionCodec("test-secret-at-least-16-characters", 1)
    monkeypatch.setattr(time, "time", lambda: 100)
    token = codec.encode(uuid.uuid4(), uuid.uuid4())
    monkeypatch.setattr(time, "time", lambda: 102)

    with pytest.raises(InvalidSessionError, match="expired"):
        codec.decode(token)


def test_invitation_configuration_rejects_required_blank_code() -> None:
    with pytest.raises(ValidationError, match="APP_INVITATION_CODE"):
        Settings(_env_file=None, invitation_required=True, invitation_code=None)


def test_invitation_proof_is_signed_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "session_secret", "test-secret-at-least-16-characters")
    monkeypatch.setattr(settings, "invitation_code", "private-invite")
    monkeypatch.setattr(settings, "invitation_ttl_seconds", 60)
    monkeypatch.setattr(auth_routes.time, "time", lambda: 1_000)

    proof = auth_routes.create_invitation_proof()

    assert auth_routes.verify_invitation_proof(proof)
    assert not auth_routes.verify_invitation_proof(proof + "tampered")
    monkeypatch.setattr(auth_routes.time, "time", lambda: 1_061)
    assert not auth_routes.verify_invitation_proof(proof)


@pytest.mark.asyncio
async def test_mock_feishu_adapter_supports_golden_flow() -> None:
    adapter = MockFeishuAdapter("http://localhost:8000")

    user = await adapter.exchange_code("mock-code", "http://localhost/callback")
    document = await adapter.fetch_document("https://example.feishu.cn/docx/demo")

    assert user.feishu_user_id == "mock_user_001"
    assert user.name == "演示售前顾问"
    assert document.content.startswith("# 客户背景")
    with pytest.raises(ValueError):
        await adapter.exchange_code("bad-code", "http://localhost/callback")


@pytest.mark.asyncio
async def test_auth_service_creates_new_user() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.get_by_feishu_user_id.return_value = None
    created_user = make_user()
    repository.create.return_value = created_user
    service = AuthService(
        session,
        MockFeishuAdapter("http://localhost"),
        settings.demo_workspace_id,
        repository,
    )

    result = await service.handle_feishu_callback("mock-code", "http://localhost/callback")

    assert result is created_user
    repository.create.assert_awaited_once_with(
        feishu_user_id="mock_user_001",
        name="演示售前顾问",
        avatar=None,
    )
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(created_user)


def test_auth_routes_and_secure_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cookie_secure", True)
    app = create_app()
    user = make_user()
    auth_service = Mock()
    auth_service.handle_feishu_callback = AsyncMock(return_value=user)
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_feishu_adapter] = lambda: MockFeishuAdapter(
        settings.public_base_url
    )

    with TestClient(app, base_url="https://testserver") as client:
        start = client.get("/api/v1/auth/feishu/start")
        assert start.status_code == 200
        assert start.json()["data"]["authorization_url"].startswith(
            f"{settings.public_base_url}/api/v1/auth/feishu/callback"
        )
        state = start.json()["data"]["state"]
        assert "HttpOnly" in start.headers["set-cookie"]
        assert "Secure" in start.headers["set-cookie"]

        invalid_callback = client.get(
            "/api/v1/auth/feishu/callback",
            params={"code": "mock-code", "state": "wrong-state"},
            follow_redirects=False,
        )
        assert invalid_callback.status_code == 400
        assert invalid_callback.json()["error"]["code"] == "VALIDATION_FAILED"

        client.cookies.set(settings.oauth_state_cookie_name, state)
        callback = client.get(
            "/api/v1/auth/feishu/callback",
            params={"code": "mock-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == settings.frontend_redirect_url
        assert settings.session_cookie_name in callback.headers["set-cookie"]

        me = client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["data"]["id"] == str(user.id)
        assert me.json()["data"]["workspace_id"] == str(user.workspace_id)

        missing_key = client.post("/api/v1/auth/logout")
        assert missing_key.status_code == 422
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Idempotency-Key": f"logout-{uuid.uuid4()}"},
        )
        assert logout.status_code == 200
        assert logout.json()["data"] == {"success": True}
        assert f"{settings.session_cookie_name}=\"\"" in logout.headers["set-cookie"]


def test_invitation_must_be_verified_before_oauth_and_is_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "invitation_required", True)
    monkeypatch.setattr(settings, "invitation_code", "private-invite")
    monkeypatch.setattr(settings, "invitation_ttl_seconds", 60)
    monkeypatch.setattr(settings, "cookie_secure", False)
    app = create_app()
    app.dependency_overrides[get_feishu_adapter] = lambda: MockFeishuAdapter(
        settings.public_base_url
    )

    with TestClient(app) as client:
        blocked = client.get("/api/v1/auth/feishu/start")
        assert blocked.status_code == 401
        assert blocked.json()["error"]["code"] == "INVITE_CODE_INVALID"

        wrong = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": "wrong"},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert wrong.status_code == 401

        verified = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": "private-invite"},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert verified.status_code == 200
        assert "HttpOnly" in verified.headers["set-cookie"]
        assert "SameSite=strict" in verified.headers["set-cookie"]

        started = client.get("/api/v1/auth/feishu/start")
        assert started.status_code == 200
        assert f"{settings.invitation_cookie_name}=\"\"" in started.headers["set-cookie"]

        blocked_again = client.get("/api/v1/auth/feishu/start")
        assert blocked_again.status_code == 401


def test_me_requires_session_cookie() -> None:
    response = TestClient(create_app()).get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
