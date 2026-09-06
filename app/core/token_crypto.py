from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import Text, TypeDecorator

from app.core.config import settings

TOKEN_PREFIX = "fernet:v1:"


class TokenEncryptionError(RuntimeError):
    pass


@lru_cache
def get_token_cipher() -> Fernet | None:
    configured = settings.secret_encryption_key or settings.feishu_token_encryption_key
    if not configured:
        return None
    try:
        return Fernet(configured.encode())
    except (TypeError, ValueError) as exc:
        raise TokenEncryptionError(
            "APP_SECRET_ENCRYPTION_KEY (or legacy APP_FEISHU_TOKEN_ENCRYPTION_KEY) "
            "must be a valid Fernet key"
        ) from exc


def encrypt_token(value: str | None) -> str | None:
    if not value or value.startswith(TOKEN_PREFIX):
        return value
    cipher = get_token_cipher()
    if cipher is None:
        if settings.enable_feishu and settings.feishu_mode == "live":
            raise TokenEncryptionError(
                "APP_SECRET_ENCRYPTION_KEY is required in live Feishu mode"
            )
        return value
    return TOKEN_PREFIX + cipher.encrypt(value.encode()).decode()


def decrypt_token(value: str | None) -> str | None:
    if not value or not value.startswith(TOKEN_PREFIX):
        return value
    cipher = get_token_cipher()
    if cipher is None:
        raise TokenEncryptionError(
            "APP_SECRET_ENCRYPTION_KEY is required to decrypt stored secrets"
        )
    try:
        return cipher.decrypt(value.removeprefix(TOKEN_PREFIX).encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionError(
            "stored Feishu token cannot be decrypted with the configured key"
        ) from exc


def validate_live_token_encryption() -> None:
    if settings.enable_feishu and settings.feishu_mode == "live" and get_token_cipher() is None:
        raise TokenEncryptionError(
            "APP_SECRET_ENCRYPTION_KEY is required in live Feishu mode"
        )


class EncryptedTokenText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return encrypt_token(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return decrypt_token(value)
