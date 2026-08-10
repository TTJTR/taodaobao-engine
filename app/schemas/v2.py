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


class CreateIntelligenceSnapshotRequest(BaseModel):
    purpose: Literal["customer_profile", "solution", "tender"]
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class CreateProfileProposalRequest(BaseModel):
    snapshot_id: uuid.UUID
    proposed_patch: dict = Field(min_length=1)


class DecideProposalRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


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


class CreateResponseMatrixRequest(BaseModel):
    experience_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    capability_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    intelligence_snapshot_id: uuid.UUID | None = None


class UpdateResponseItemRequest(BaseModel):
    response_text: str | None = Field(default=None, min_length=1, max_length=20_000)
    risks: list[str] | None = Field(default=None, max_length=20)


class ReviewResponseItemRequest(BaseModel):
    action: Literal["accept", "reject", "needs_revision"]
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
