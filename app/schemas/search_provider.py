from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class SearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    url: AnyHttpUrl
    snippet: str | None = Field(default=None, max_length=10_000)
    site_name: str | None = Field(default=None, max_length=200)
    position: int = Field(ge=0)


class SearchDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_request_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2_000)
    sources: list[SearchSource] = Field(min_length=1, max_length=20)
    answer_summary: str | None = Field(default=None, max_length=50_000)
    usage: dict[str, Any] = Field(default_factory=dict)
