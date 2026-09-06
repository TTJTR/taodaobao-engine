import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ModelCapability = Literal["ai", "interactive-html", "web-search"]
ModelProvider = Literal["dashscope", "deepseek", "openai-compatible"]


class ConfigureModelConnectionRequest(BaseModel):
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_key: str = Field(min_length=8, max_length=4096)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Base URL 必须是有效的 HTTP/HTTPS 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Base URL 不得包含凭据、查询参数或片段")
        if parsed.scheme == "http" and not _is_private_http_host(parsed.hostname):
            raise ValueError("公网模型服务必须使用 HTTPS")
        return normalized


def _is_private_http_host(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in {"localhost", "host.docker.internal"} or lowered.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_private or ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False
