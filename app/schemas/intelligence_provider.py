import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class ProviderCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    quote: str = Field(min_length=1, max_length=10_000)
    title: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    provider_confidence: float | None = Field(default=None, ge=0, le=1)


class EnrichmentFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    value: str | int | float | bool | list[str]
    category: Literal[
        "company_profile",
        "buying_signal",
        "hiring_signal",
        "technology_signal",
        "project_signal",
        "risk_signal",
        "competitive_signal",
    ]
    provider_confidence: float = Field(ge=0, le=1)
    citations: list[ProviderCitation] = Field(min_length=1, max_length=20)


class EnrichmentJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_job_id: uuid.UUID
    company_name: str = Field(min_length=1, max_length=500)
    website_url: AnyHttpUrl | None = None
    allowed_fields: list[str] = Field(min_length=1, max_length=20)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    country: str | None = Field(default="CN", min_length=2, max_length=2)
    max_tool_calls: int = Field(default=50, ge=1, le=200)
    max_cost_usd: float = Field(default=2.0, gt=0, le=100)

    @model_validator(mode="after")
    def unique_allowed_fields(self) -> "EnrichmentJobRequest":
        if len(self.allowed_fields) != len(set(self.allowed_fields)):
            raise ValueError("allowed_fields must be unique")
        return self


class EnrichmentJobAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_job_id: str = Field(min_length=1, max_length=200)
    status: Literal["queued"] = "queued"
    accepted_at: datetime


class EnrichmentJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_job_id: str
    status: Literal["queued", "processing", "completed", "partial", "failed"]
    stage: str = Field(min_length=1, max_length=64)
    retryable: bool = False
    error_code: str | None = Field(default=None, max_length=64)
    error_summary: str | None = Field(default=None, max_length=1000)
    tool_calls_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)


class EnrichmentJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_job_id: str
    status: Literal["completed", "partial"]
    facts: list[EnrichmentFact] = Field(min_length=1, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    tool_calls_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
