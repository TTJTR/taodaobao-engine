from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APP_",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    ai_mode: Literal["mock", "live"] = "mock"
    rehearsal_ai_timeout_seconds: float = 20.0
    rehearsal_ai_max_prompt_characters: int = 36_000
    feishu_mode: Literal["mock", "live"] = "mock"
    presentation_mode: Literal["mock", "live"] = "mock"
    presentation_service_url: str | None = None
    presentation_service_api_key: str | None = None
    database_url: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_verification_token: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_token_encryption_key: str | None = None
    feishu_scopes: str = (
        "offline_access docx:document space:document:retrieve "
        "minutes:minutes.search:read minutes:minutes.basic:read "
        "minutes:minutes.transcript:export"
    )
    invitation_required: bool = False
    invitation_code: str | None = None
    invitation_signing_secret: str | None = None
    invitation_cookie_name: str = "taodaobao_invitation"
    invitation_ttl_seconds: int = 10 * 60
    invitation_max_token_ttl_seconds: int = 7 * 24 * 60 * 60
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_redirect_url: str = "http://127.0.0.1:8000/"
    demo_workspace_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    session_secret: str = "development-only-change-me"
    session_cookie_name: str = "taodaobao_session"
    oauth_state_cookie_name: str = "taodaobao_oauth_state"
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.invitation_required and not (
            (self.invitation_signing_secret or "").strip() or (self.invitation_code or "").strip()
        ):
            raise ValueError(
                "APP_INVITATION_SIGNING_SECRET or APP_INVITATION_CODE is required "
                "when APP_INVITATION_REQUIRED=true"
            )
        if self.invitation_signing_secret and len(self.invitation_signing_secret) < 32:
            raise ValueError("APP_INVITATION_SIGNING_SECRET must contain at least 32 characters")
        if self.invitation_ttl_seconds <= 0:
            raise ValueError("APP_INVITATION_TTL_SECONDS must be positive")
        if self.invitation_max_token_ttl_seconds <= 0:
            raise ValueError("APP_INVITATION_MAX_TOKEN_TTL_SECONDS must be positive")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
