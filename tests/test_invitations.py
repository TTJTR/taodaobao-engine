import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.deps import get_feishu_adapter, get_invitation_redemption_store
from app.core.config import settings
from app.core.invitations import (
    generate_invitation_token,
    generate_short_invitation_code,
    hash_short_invitation_code,
    normalize_short_invitation_code,
    verify_invitation_token,
)
from app.integrations import MockFeishuAdapter
from app.main import create_app
from app.services.invitations import RegisteredInvitationRedemption


class MemoryRedemptionStore:
    def __init__(self) -> None:
        self.redeemed: dict[str, int] = {}
        self.max_uses: dict[str, int] = {}
        self.consumed: set[tuple[str, int]] = set()
        self.registered: dict[str, tuple[datetime, int]] = {}

    async def redeem(
        self, token_id_hash: str, expires_at: datetime, max_uses: int
    ) -> int | None:
        del expires_at
        if token_id_hash in self.max_uses and self.max_uses[token_id_hash] != max_uses:
            return None
        current = self.redeemed.get(token_id_hash, 0)
        if current >= max_uses:
            return None
        self.max_uses[token_id_hash] = max_uses
        self.redeemed[token_id_hash] = current + 1
        return current + 1

    async def redeem_registered(
        self, token_id_hash: str
    ) -> RegisteredInvitationRedemption | None:
        registration = self.registered.get(token_id_hash)
        if registration is None:
            return None
        expires_at, max_uses = registration
        if expires_at < datetime.now(UTC):
            return None
        redemption_number = await self.redeem(token_id_hash, expires_at, max_uses)
        if redemption_number is None:
            return None
        return RegisteredInvitationRedemption(redemption_number, expires_at)

    async def consume(self, token_id_hash: str, redemption_number: int) -> bool:
        key = (token_id_hash, redemption_number)
        if redemption_number > self.redeemed.get(token_id_hash, 0) or key in self.consumed:
            return False
        self.consumed.add(key)
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


def test_signed_invitation_carries_bounded_max_uses() -> None:
    secret = "test-signing-secret-that-is-long-enough"
    token = generate_invitation_token(secret, ttl_seconds=60, max_uses=20, now=1_000)

    claims = verify_invitation_token(token, secret, max_ttl_seconds=60, now=1_000)
    assert claims is not None
    assert claims.max_uses == 20


def test_short_invitation_format_is_memorable_and_normalized() -> None:
    code = generate_short_invitation_code()

    assert len(code) == 9
    assert code.startswith("tdb-")
    assert normalize_short_invitation_code(f"  {code.upper()}  ") == code
    assert normalize_short_invitation_code("tdb-o0i1l") is None


def test_short_invitation_hash_is_secret_bound_and_case_insensitive() -> None:
    secret = "test-signing-secret-that-is-long-enough"
    code = "tdb-k7m2q"

    assert hash_short_invitation_code(code, secret) == hash_short_invitation_code(
        code.upper(), secret
    )
    assert hash_short_invitation_code(code, secret) != hash_short_invitation_code(
        code, "another-signing-secret-that-is-long-enough"
    )


def test_legacy_signed_invitation_defaults_to_single_use() -> None:
    secret = "test-signing-secret-that-is-long-enough"
    token = generate_invitation_token(secret, ttl_seconds=60, now=1_000)

    claims = verify_invitation_token(token, secret, max_ttl_seconds=60, now=1_000)
    assert claims is not None
    assert claims.max_uses == 1


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


def test_signed_invitation_allows_configured_number_of_users(monkeypatch) -> None:
    secret = "test-signing-secret-that-is-long-enough"
    monkeypatch.setattr(settings, "invitation_required", True)
    monkeypatch.setattr(settings, "invitation_signing_secret", secret)
    monkeypatch.setattr(settings, "invitation_max_token_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "invitation_ttl_seconds", 600)
    monkeypatch.setattr(settings, "cookie_secure", False)
    token = generate_invitation_token(
        secret,
        ttl_seconds=3600,
        max_uses=3,
        now=int(time.time()),
    )
    store = MemoryRedemptionStore()
    app = create_app()
    app.dependency_overrides[get_invitation_redemption_store] = lambda: store
    app.dependency_overrides[get_feishu_adapter] = lambda: MockFeishuAdapter(
        settings.public_base_url
    )

    for _ in range(3):
        with TestClient(app) as client:
            verified = client.post(
                "/api/v1/auth/invitation/verify",
                json={"invitation_code": token},
                headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
            )
            assert verified.status_code == 200
            assert client.get("/api/v1/auth/feishu/start").status_code == 200
            assert client.get("/api/v1/auth/feishu/start").status_code == 401

    with TestClient(app) as client:
        exhausted = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": token},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert exhausted.status_code == 401


def test_registered_short_invitation_allows_configured_number_of_users(
    monkeypatch,
) -> None:
    secret = "test-signing-secret-that-is-long-enough"
    code = "tdb-k7m2q"
    code_hash = hash_short_invitation_code(code, secret)
    assert code_hash is not None
    monkeypatch.setattr(settings, "invitation_required", True)
    monkeypatch.setattr(settings, "invitation_signing_secret", secret)
    monkeypatch.setattr(settings, "invitation_ttl_seconds", 600)
    monkeypatch.setattr(settings, "cookie_secure", False)
    store = MemoryRedemptionStore()
    store.registered[code_hash] = (datetime.now(UTC) + timedelta(days=20), 3)
    app = create_app()
    app.dependency_overrides[get_invitation_redemption_store] = lambda: store
    app.dependency_overrides[get_feishu_adapter] = lambda: MockFeishuAdapter(
        settings.public_base_url
    )

    for submitted_code in (code, code.upper(), f"  {code}  "):
        with TestClient(app) as client:
            verified = client.post(
                "/api/v1/auth/invitation/verify",
                json={"invitation_code": submitted_code},
                headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
            )
            assert verified.status_code == 200
            assert client.get("/api/v1/auth/feishu/start").status_code == 200

    with TestClient(app) as client:
        exhausted = client.post(
            "/api/v1/auth/invitation/verify",
            json={"invitation_code": code},
            headers={"Idempotency-Key": f"invite-{uuid.uuid4()}"},
        )
        assert exhausted.status_code == 401
