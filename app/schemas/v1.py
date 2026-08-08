import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    CollaborationStatus,
    ExpertQuestionStatus,
    ProcessStatus,
    ResearchTaskStatus,
)


class CreateResearchTaskRequest(BaseModel):
    customer_profile_id: uuid.UUID
    session_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    question: str = Field(min_length=1, max_length=10_000)
    completion_conditions: list[str] = Field(default_factory=list, max_length=8)


class CancelResearchTaskRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class ResearchStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    stage: str
    status: ProcessStatus
    attempt: int
    output_snapshot: dict | None
    error_code: str | None
    error_summary: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ResearchTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_profile_id: uuid.UUID
    session_id: uuid.UUID | None
    title: str
    question: str
    completion_conditions: list[str]
    status: ResearchTaskStatus
    stage: str
    progress: int
    trace_id: str
    evidence_snapshot: dict | None
    research_plan: dict | None
    findings: list[dict]
    routes: list[dict]
    audit: dict | None
    knowledge_gaps: list[str]
    expert_questions: list[dict]
    report: dict | None
    research_document_url: str | None
    error_code: str | None
    error_summary: str | None
    retry_count: int
    cancel_reason: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ResearchTaskDetail(ResearchTaskRead):
    profile_snapshot: dict
    conversation_snapshot: list[dict]
    steps: list[ResearchStepRead] = Field(default_factory=list)


class ResearchContextRead(BaseModel):
    research_task_id: uuid.UUID
    conversation: list[dict]
    research_document: dict | None
    research_document_url: str | None
    customer_profile: dict
    evidence_snapshot: dict
    knowledge_gaps: list[str]
    pending_questions: list[dict]


class CollaborationQuestion(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    required_roles: list[str] = Field(default_factory=list, max_length=3)
    sensitive: bool = False


class CreateExpertCollaborationRequest(BaseModel):
    research_task_id: uuid.UUID


class UpdateExpertCollaborationRequest(BaseModel):
    group_name: str = Field(min_length=1, max_length=200)
    selected_expert_ids: list[uuid.UUID] = Field(default_factory=list, max_length=3)
    questions: list[CollaborationQuestion] = Field(min_length=1, max_length=8)


class ExpertCollaborationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    research_task_id: uuid.UUID
    status: CollaborationStatus
    group_name: str
    candidate_records: list[dict]
    selected_expert_ids: list[str]
    questions: list[dict]
    context_snapshot: dict
    content_bundle: dict | None
    feishu_document_id: str | None
    feishu_document_url: str | None
    feishu_group_id: str | None
    error_code: str | None
    error_summary: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    closed_at: datetime | None


class ExpertReplyEvent(BaseModel):
    collaboration_id: uuid.UUID
    question_id: str = Field(min_length=1, max_length=128)
    author_id: str = Field(min_length=1, max_length=128)
    author_name: str = Field(min_length=1, max_length=200)
    answer_text: str = Field(min_length=1, max_length=20_000)
    feishu_message_id: str = Field(min_length=1, max_length=256)
    message_url: str = Field(min_length=1, max_length=2000)
    verification_token: str | None = Field(default=None, max_length=512)


class ExpertReplyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    research_task_id: uuid.UUID
    collaboration_id: uuid.UUID
    question_id: str
    author_id: str
    author_name: str
    answer_text: str
    feishu_message_id: str
    message_url: str
    status: ExpertQuestionStatus
    adopted_experience_id: uuid.UUID | None
    created_at: datetime


class AdoptExpertReplyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    applicable_problem: str = Field(min_length=1, max_length=2000)
    solution: str = Field(min_length=1, max_length=10_000)
    prerequisites: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=20)


class FeishuResourceRead(BaseModel):
    token: str
    title: str
    resource_type: str
    url: str
    owner_name: str | None = None
    updated_at: datetime | None = None
