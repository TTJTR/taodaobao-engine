from typing import Literal

from pydantic import Field, model_validator

from app.ai.schemas.assets import CustomerProfileDraft
from app.ai.schemas.base import AISchema, NonEmptyStr
from app.ai.schemas.research import (
    CandidateRecommendation,
    CandidateRecord,
    ExpertAnswerInput,
    ExpertQuestion,
    ResearchContextPackage,
    ResearchFinding,
    ResearchMode,
    ResearchPlan,
    ResearchStage,
)


class ConversationMessage(AISchema):
    role: Literal["user", "assistant"]
    content: NonEmptyStr


class SolutionContext(AISchema):
    schema_version: Literal["solution-v1", "solution-v2"] = "solution-v1"
    customer_profile: CustomerProfileDraft
    current_requirement: NonEmptyStr
    opening_line: NonEmptyStr | None = None
    trace_id: NonEmptyStr | None = None
    mode: ResearchMode = ResearchMode.QUICK
    stage: ResearchStage | None = None
    research_task_id: NonEmptyStr | None = None
    research_plan: ResearchPlan | None = None
    prior_findings: list[ResearchFinding] = Field(default_factory=list)
    expert_answers: list[ExpertAnswerInput] = Field(default_factory=list)
    task_type: Literal["solution", "expert_routing"] = "solution"
    research_context: ResearchContextPackage | None = None
    candidate_records: list[CandidateRecord] = Field(default_factory=list)
    conversation_history: list[ConversationMessage] = Field(
        default_factory=list,
        max_length=20,
    )
    deadline_at: str | None = None
    retry_budget: int = Field(default=0, ge=0, le=3)

    @model_validator(mode="after")
    def v1_context_requires_research_identity(self) -> "SolutionContext":
        if self.mode == ResearchMode.DEEP and not (self.research_task_id and self.stage):
            raise ValueError("deep mode requires research_task_id and stage")
        if self.task_type == "expert_routing":
            if self.research_context is None:
                raise ValueError("expert_routing requires research_context")
            if self.research_task_id != self.research_context.research_task_id:
                raise ValueError("expert_routing research_task_id must match research_context")
        return self


class SearchIntent(AISchema):
    current_requirement: NonEmptyStr
    query_text: NonEmptyStr
    business_goal: NonEmptyStr | None = None
    industry: NonEmptyStr | None = None
    scenarios: list[NonEmptyStr] = Field(default_factory=list)
    problems: list[NonEmptyStr] = Field(default_factory=list)
    goals: list[NonEmptyStr] = Field(default_factory=list)
    hard_constraints: list[NonEmptyStr] = Field(default_factory=list)
    keywords: list[NonEmptyStr] = Field(default_factory=list)
    preferred_tags: list[NonEmptyStr] = Field(default_factory=list)
    excluded_claims: list[NonEmptyStr] = Field(default_factory=list)
    missing_information: list[NonEmptyStr] = Field(default_factory=list)
    experience_query_text: NonEmptyStr
    capability_query_text: NonEmptyStr
    ranking_signals: list[NonEmptyStr] = Field(default_factory=list)
    expertise_gaps: list[NonEmptyStr] = Field(default_factory=list)
    ranked_candidate_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=3)
    candidate_reasons: list[CandidateRecommendation] = Field(default_factory=list, max_length=3)
    expert_questions: list[ExpertQuestion] = Field(default_factory=list, max_length=8)
    do_not_invite_reason: NonEmptyStr | None = None
    embedding_text: NonEmptyStr
