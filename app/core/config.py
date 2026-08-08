from functools import lru_cache
from typing import Literal
from uuid import UUID

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
    feishu_mode: Literal["mock", "live"] = "mock"
    database_url: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_verification_token: str | None = None
    invitation_required: bool = False
    invitation_code: str | None = None
    invitation_cookie_name: str = "taodaobao_invitation"
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_redirect_url: str = "http://127.0.0.1:8000/"
    demo_workspace_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    session_secret: str = "development-only-change-me"
    session_cookie_name: str = "taodaobao_session"
    oauth_state_cookie_name: str = "taodaobao_oauth_state"
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
