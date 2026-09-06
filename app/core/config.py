import base64
import binascii
from functools import lru_cache
from pathlib import Path
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
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_port: int = 3000
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    redis_url: str | None = None
    auth_mode: Literal["local", "feishu", "both"] = "feishu"
    local_admin_username: str | None = None
    local_admin_password: str | None = None
    local_workspace_id: UUID = UUID("00000000-0000-4000-8000-000000000010")
    enable_feishu: bool = True
    enable_public_intelligence: bool = True
    enable_demo_data: bool = False
    ai_mode: Literal["live"] = "live"
    rehearsal_ai_timeout_seconds: float = 20.0
    rehearsal_ai_max_prompt_characters: int = 36_000
    feishu_mode: Literal["live"] = "live"
    presentation_mode: Literal["live"] = "live"
    presentation_service_url: str | None = None
    presentation_service_api_key: str | None = None
    interactive_html_mode: Literal["live"] = "live"
    interactive_html_base_url: str = "https://api.deepseek.com"
    interactive_html_api_key: str | None = None
    interactive_html_model: str = "deepseek-chat"
    interactive_html_timeout_seconds: float = 180.0
    presentation_export_dir: Path = Path(".local/exports")
    pptx_renderer_timeout_seconds: int = 30
    pptx_renderer_max_output_bytes: int = 50 * 1024 * 1024
    database_url: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_callback_url: str | None = None
    feishu_verification_token: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_token_encryption_key: str | None = None
    secret_encryption_key: str | None = None
    feishu_scopes: str = (
        "offline_access docx:document space:document:retrieve wiki:wiki:readonly "
        "docs:permission.member:retrieve bitable:app "
        "minutes:minutes.search:read minutes:minutes.basic:read "
        "minutes:minutes.transcript:export"
    )
    invitation_required: bool = False
    invitation_code: str | None = None
    invitation_signing_secret: str | None = None
    invitation_cookie_name: str = "taodaobao_invitation"
    invitation_ttl_seconds: int = 10 * 60
    invitation_max_token_ttl_seconds: int = 30 * 24 * 60 * 60
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_redirect_url: str = "http://127.0.0.1:8000/"
    demo_workspace_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    session_secret: str = "development-only-change-me"
    session_cookie_name: str = "taodaobao_session"
    oauth_state_cookie_name: str = "taodaobao_oauth_state"
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = True
    tender_storage_root: Path = Path(".local/tender-artifacts")
    tender_parser_backend: Literal["lightweight", "docling"] = "lightweight"
    open_enrich_svc_url: str | None = None
    open_enrich_svc_token: str | None = None
    open_enrich_poll_seconds: int = 10
    bailian_search_api_key: str | None = None
    bailian_search_model: str = "qwen-plus"
    bailian_search_base_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    )
    bailian_search_strategy: Literal["turbo", "max", "agent", "agent_max"] = "turbo"
    bailian_search_timeout_seconds: float = 45.0
    bailian_search_max_results: int = 8
    usage_monitor_token: str | None = None
    integration_state_dir: Path = Path(".local/integrations")
    file_storage_root: Path = Path(".local/uploads")

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [item.strip().rstrip("/") for item in self.cors_origins.split(",") if item.strip()]

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if self.host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("APP_HOST must be 127.0.0.1 or 0.0.0.0")
        if not 1 <= self.port <= 65535 or not 1 <= self.frontend_port <= 65535:
            raise ValueError("APP_PORT and APP_FRONTEND_PORT must be valid TCP ports")
        if self.auth_mode in {"local", "both"}:
            if not (self.local_admin_username or "").strip():
                raise ValueError("APP_LOCAL_ADMIN_USERNAME is required for local authentication")
            if len(self.local_admin_password or "") < 12:
                raise ValueError("APP_LOCAL_ADMIN_PASSWORD must contain at least 12 characters")
            if self.env == "production" and "replace-with" in (self.local_admin_password or ""):
                raise ValueError("APP_LOCAL_ADMIN_PASSWORD still contains a placeholder")
        if self.env == "production" and self.session_secret == "development-only-change-me":
            raise ValueError("APP_SESSION_SECRET must be changed in production")
        if self.env == "production" and (
            len(self.session_secret) < 32 or "replace-with" in self.session_secret
        ):
            raise ValueError("APP_SESSION_SECRET must be a non-placeholder value of 32+ characters")
        if self.env == "production":
            encryption_key = self.secret_encryption_key or self.feishu_token_encryption_key or ""
            try:
                decoded_key = base64.urlsafe_b64decode(encryption_key.encode())
            except (binascii.Error, ValueError):
                decoded_key = b""
            if len(decoded_key) != 32:
                raise ValueError("APP_SECRET_ENCRYPTION_KEY must be a valid Fernet key")
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
        if self.open_enrich_poll_seconds <= 0:
            raise ValueError("APP_OPEN_ENRICH_POLL_SECONDS must be positive")
        if self.interactive_html_timeout_seconds <= 0:
            raise ValueError("APP_INTERACTIVE_HTML_TIMEOUT_SECONDS must be positive")
        if self.bailian_search_timeout_seconds <= 0:
            raise ValueError("APP_BAILIAN_SEARCH_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.bailian_search_max_results <= 20:
            raise ValueError("APP_BAILIAN_SEARCH_MAX_RESULTS must be between 1 and 20")
        if self.usage_monitor_token and len(self.usage_monitor_token) < 32:
            raise ValueError("APP_USAGE_MONITOR_TOKEN must contain at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
