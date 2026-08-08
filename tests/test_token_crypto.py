import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.token_crypto import (
    TOKEN_PREFIX,
    EncryptedTokenText,
    TokenEncryptionError,
    decrypt_token,
    encrypt_token,
    get_token_cipher,
    validate_live_token_encryption,
)
from app.db.models import User


@pytest.fixture(autouse=True)
def reset_cipher_cache():
    get_token_cipher.cache_clear()
    yield
    get_token_cipher.cache_clear()


def test_feishu_token_round_trip_is_encrypted_at_rest(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_mode", "live")
    monkeypatch.setattr(
        settings,
        "feishu_token_encryption_key",
        Fernet.generate_key().decode(),
    )

    stored = encrypt_token("u-sensitive-token")

    assert stored is not None
    assert stored.startswith(TOKEN_PREFIX)
    assert "u-sensitive-token" not in stored
    assert decrypt_token(stored) == "u-sensitive-token"
    assert isinstance(User.__table__.c.feishu_access_token.type, EncryptedTokenText)
    assert isinstance(User.__table__.c.feishu_refresh_token.type, EncryptedTokenText)


def test_live_mode_rejects_missing_token_encryption_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_mode", "live")
    monkeypatch.setattr(settings, "feishu_token_encryption_key", None)

    with pytest.raises(TokenEncryptionError, match="required"):
        validate_live_token_encryption()
    with pytest.raises(TokenEncryptionError, match="required"):
        encrypt_token("u-sensitive-token")


def test_mock_mode_keeps_legacy_plaintext_compatible(monkeypatch) -> None:
    monkeypatch.setattr(settings, "feishu_mode", "mock")
    monkeypatch.setattr(settings, "feishu_token_encryption_key", None)

    assert encrypt_token("mock-token") == "mock-token"
    assert decrypt_token("legacy-plain-token") == "legacy-plain-token"
