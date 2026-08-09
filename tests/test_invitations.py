import time
import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app.api.deps import get_feishu_adapter, get_invitation_redemption_store
from app.core.config import settings
from app.core.invitations import generate_invitation_token, verify_invitation_token
from app.integrations import MockFeishuAdapter
from app.main import create_app


class MemoryRedemptionStore:
    def __init__(self) -> None:
        self.redeemed: set[str] = set()
        self.consumed: set[str] = set()

    async def redeem(self, token_id_hash: str, expires_at: datetime) -> bool:
        del expires_at
        if token_id_hash in self.redeemed:
            return False
        self.redeemed.add(token_id_hash)
        return True

    async def consume(self, token_id_hash: str) -> bool:
        if token_id_hash not in self.redeemed or token_id_hash in self.consumed:
            return False
        self.consumed.add(token_id_hash)
        return True


def test_signed_invitation_rejects_tampering_expiry_and_excessive_ttl() -> None:
    secret = "test-signing-secret-that-is-long-enough"
    token = generate_invitation_token(secret, ttl_seconds=60, now=1_000)

    assert verify_invitation_token(token, secret, max_ttl_seconds=60, now=1_000)
    assert not verify_invitation_token(token + "x", secret, max_ttl_seconds=60, now=1_000)
    assert not verify_invitation_token(token, secret, max_ttl_seconds=59, now=1_000)
    assert not verify_invitation_token(token, secret, max_ttl_seconds=60, now=1_061)


def test_signed_invitation_supports_thirty_day_operator_window() -> None:
    secret = "test-signing-secret-that-is-long-enough"
    thirty_days = 30 * 24 * 60 * 60
    token = generate_invitation_token(secret, ttl_seconds=thirty_days, now=1_000)

    assert verify_invitation_token(
        token, secret, max_ttl_seconds=thirty_days, now=1_000
    )


def test_signed_invitation_is_single_use_across_verification_and_oauth(
    monkeypatch,
) -> None:
    secret = "test-signing-secret-that-is-long-enough"
    monkeypatch.setattr(settings, "invitation_required", True)
    monkeypatch.setattr(settings, "invitation_signing_secret", secret)
    monkeypatch.setattr(settings, "invitation_max_token_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "invitation_ttl_seconds", 600)
    monkeypatch.setattr(settings, "cookie_secure", False)
    token = generate_invitation_token(secret, ttl_seconds=3600, now=int(time.time()))
    store = MemoryRedemptionStore()
    app = create_app()
    app.dependency_overrides[get_invitation_redemption_store] = lambda: store
    app.dependency_overrides[get_feishu_adapter] = lambda: MockFeishuAdapter(
        settings.public_base_url
    )

    with TestClient(app) as client:
        verified = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": token},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert verified.status_code == 200
        started = client.get("/api/v1/auth/feishu/start")
        assert started.status_code == 200
        assert client.get("/api/v1/auth/feishu/start").status_code == 401

        repeated = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": token},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert repeated.status_code == 401
