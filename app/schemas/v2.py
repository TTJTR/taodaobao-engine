import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ManualIntelligenceSource(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    content: str = Field(min_length=1, max_length=200_000)
    published_at: datetime | None = None


class CreateSearchRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    purpose: Literal["customer_profile", "solution", "tender"]
    provider: Literal["manual"] = "manual"
    sources: list[ManualIntelligenceSource] = Field(min_length=1, max_length=20)


class CreateAutomaticSearchRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    purpose: Literal["customer_profile", "solution", "tender"]
    profile_id: uuid.UUID | None = None
    max_results: int = Field(default=5, ge=1, le=10)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    country: str | None = Field(default="CN", min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_profile_for_profile_search(self) -> "CreateAutomaticSearchRunRequest":
        if self.purpose == "customer_profile" and self.profile_id is None:
            raise ValueError("profile_id is required for customer_profile search")
        return self


class CreateIntelligenceSearchTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    purpose: Literal["customer_profile", "solution", "tender"]
    query_template: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    allowed_fields: list[str] = Field(default_factory=list, max_length=20)


class UpdateIntelligenceSearchTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    query_template: str | None = Field(default=None, min_length=1, max_length=2000)
    keywords: list[str] | None = Field(default=None, max_length=20)
    allowed_fields: list[str] | None = Field(default=None, max_length=20)


class CreateIntelligenceSnapshotRequest(BaseModel):
    purpose: Literal["customer_profile", "solution", "tender"]
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class ReassessIntelligenceFreshnessRequest(BaseModel):
    stale_after_days: int = Field(default=90, ge=1, le=365)


class CreateProfileProposalRequest(BaseModel):
    snapshot_id: uuid.UUID
    proposed_patch: dict = Field(min_length=1)


class EnrichRawArtifactRequest(BaseModel):
    profile_id: uuid.UUID


class QueueProviderEnrichmentRequest(BaseModel):
    profile_id: uuid.UUID
    company_name: str = Field(min_length=1, max_length=500)
    website_url: HttpUrl | None = None
    allowed_fields: list[str] = Field(min_length=1, max_length=20)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    country: str | None = Field(default="CN", min_length=2, max_length=2)
    max_tool_calls: int = Field(default=50, ge=1, le=200)
    max_cost_usd: float = Field(default=2.0, gt=0, le=100)


class DecideProposalRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    selected_candidates: dict[str, uuid.UUID] = Field(default_factory=dict, max_length=50)


class CreateTenderRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    customer_profile_id: uuid.UUID | None = None
    pasted_text: str | None = Field(default=None, max_length=1_000_000)
    source_filename: str | None = Field(default=None, max_length=500)
    source_mime_type: str | None = Field(default=None, max_length=128)
    content_base64: str | None = Field(default=None, max_length=8_000_000)

    @model_validator(mode="after")
    def require_content(self) -> "CreateTenderRequest":
        if not self.pasted_text and not self.content_base64:
            raise ValueError("pasted_text or content_base64 is required")
        return self


class QueueTenderParseRequest(BaseModel):
    raw_artifact_id: uuid.UUID


class UpdateTenderRequirementRequest(BaseModel):
    requirement_text: str | None = Field(default=None, min_length=1, max_length=20_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    mandatory: bool | None = None
    acceptance_condition: str | None = Field(default=None, max_length=20_000)
    constraints: dict | None = None
    metrics: list[dict] | None = Field(default=None, max_length=100)
    ambiguities: list[str] | None = Field(default=None, max_length=100)
    recommended_action: str | None = Field(default=None, max_length=20_000)
    expected_version: int = Field(ge=1)


class RequirementVersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class MergeTenderRequirementsRequest(BaseModel):
    requirement_ids: list[uuid.UUID] = Field(min_length=2, max_length=50)
    expected_versions: dict[str, int] = Field(min_length=2, max_length=50)
    requirement_text: str = Field(min_length=1, max_length=20_000)


class SplitTenderRequirementItem(BaseModel):
    requirement_text: str = Field(min_length=1, max_length=20_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    mandatory: bool | None = None
    acceptance_condition: str | None = Field(default=None, max_length=20_000)
    metrics: list[dict] | None = Field(default=None, max_length=100)
    ambiguities: list[str] | None = Field(default=None, max_length=100)
    recommended_action: str | None = Field(default=None, max_length=20_000)


class SplitTenderRequirementRequest(BaseModel):
    expected_version: int = Field(ge=1)
    items: list[SplitTenderRequirementItem] = Field(min_length=2, max_length=50)


class CreateResponseMatrixRequest(BaseModel):
    experience_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    capability_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    intelligence_snapshot_id: uuid.UUID | None = None


class UpdateResponseItemRequest(BaseModel):
    response_text: str | None = Field(default=None, min_length=1, max_length=20_000)
    risks: list[str] | None = Field(default=None, max_length=20)
    expected_version: int = Field(ge=1)


class ReviewResponseItemRequest(BaseModel):
    action: Literal["approve", "edit_and_approve", "reject", "needs_evidence"]
    expected_version: int = Field(ge=1)
    current_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
    note: str | None = Field(default=None, max_length=1000)


class BatchReviewResponseItemsRequest(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["approve", "reject", "needs_evidence"]
    expected_versions: dict[str, int] = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class CreateRehearsalRequest(BaseModel):
    customer_profile_id: uuid.UUID
    title: str | None = Field(default=None, max_length=300)
    solution_run_id: uuid.UUID | None = None
    research_task_id: uuid.UUID | None = None
    intelligence_snapshot_id: uuid.UUID | None = None
    response_matrix_id: uuid.UUID | None = None
    role: Literal["customer_decision_maker", "technical_reviewer", "procurement", "challenger"]
    difficulty: Literal["easy", "standard", "hard"] = "standard"
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    max_turns: int = Field(default=5, ge=3, le=10)

    @model_validator(mode="after")
    def require_solution_context(self) -> "CreateRehearsalRequest":
        if not self.solution_run_id and not self.research_task_id:
            raise ValueError("solution_run_id or research_task_id is required")
        return self


class SubmitRehearsalTurnRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)


class RuntimeTaskRead(BaseModel):
    id: uuid.UUID
    task_type: str
    title: str
    status: str
    stage: str
    progress: int = Field(ge=0, le=100)
    trace_id: str | None
    trust_action: str | None = None
    output_summary: dict = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime


class OrmReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
