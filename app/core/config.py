from functools import lru_cache
from typing import Literal

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
    database_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

