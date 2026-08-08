from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from app.ai.schemas.base import AISchema, NonEmptyStr
from app.ai.schemas.retrieval import RetrievalSnapshot
from app.ai.schemas.solution import EvidenceBoundary, Solution


class ResearchMode(StrEnum):
    QUICK = "quick"
    DEEP = "deep"


class ResearchStage(StrEnum):
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"
    ROUTE_COMPARISON = "route_comparison"
    FACT_AUDIT = "fact_audit"
    AWAITING_EXPERT = "awaiting_expert"
    FINAL_REPORT = "final_report"


class ResearchItemStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class ResearchTaskStatus(StrEnum):
    RUNNING = "running"
    AWAITING_EXPERT = "awaiting_expert"
    COMPLETED = "completed"
    NEEDS_RETRY = "needs_retry"


class ResearchSubquestion(AISchema):
    question_id: NonEmptyStr
    question: NonEmptyStr
    completion_condition: NonEmptyStr
    status: ResearchItemStatus = ResearchItemStatus.PENDING


class ResearchPlan(AISchema):
    objective: NonEmptyStr
    subquestions: list[ResearchSubquestion] = Field(min_length=1, max_length=6)
    completion_conditions: list[NonEmptyStr] = Field(min_length=1, max_length=8)


class ResearchFinding(AISchema):
    finding_id: NonEmptyStr
    text: NonEmptyStr
    boundary: EvidenceBoundary
    asset_id: NonEmptyStr | None = None
    source_id: NonEmptyStr | None = None
    stage: ResearchStage

    @model_validator(mode="after")
    def sourced_boundaries_need_both_ids(self) -> "ResearchFinding":
        sourced = self.boundary in {
            EvidenceBoundary.HISTORICAL_FACT,
            EvidenceBoundary.ENTERPRISE_CAPABILITY,
        }
        has_any_id = bool(self.asset_id or self.source_id)
        has_both_ids = bool(self.asset_id and self.source_id)
        if (sourced and not has_both_ids) or (not sourced and has_any_id):
            raise ValueError("sourced research findings require asset_id and source_id")
        return self


class ResearchRoute(AISchema):
    route_id: NonEmptyStr
    name: NonEmptyStr
    summary: NonEmptyStr
    tradeoffs: list[NonEmptyStr] = Field(default_factory=list, max_length=6)
    unsuitable_conditions: list[NonEmptyStr] = Field(default_factory=list, max_length=6)
    supporting_asset_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=10)


class ResearchAuditIssue(AISchema):
    issue_type: NonEmptyStr
    description: NonEmptyStr
    affected_finding_ids: list[NonEmptyStr] = Field(default_factory=list)
    required_action: NonEmptyStr


class ResearchAudit(AISchema):
    passed: bool
    issues: list[ResearchAuditIssue] = Field(default_factory=list)
    knowledge_gaps: list[NonEmptyStr] = Field(default_factory=list)


class ExpertAnswerInput(AISchema):
    research_task_id: NonEmptyStr
    question_id: NonEmptyStr
    author_id: NonEmptyStr
    author_name: NonEmptyStr
    message_url: NonEmptyStr
    answer_text: NonEmptyStr
    source_ids: list[NonEmptyStr] = Field(default_factory=list)
    boundary: EvidenceBoundary = EvidenceBoundary.PENDING_CONFIRMATION

    @model_validator(mode="after")
    def answer_stays_pending(self) -> "ExpertAnswerInput":
        if self.boundary != EvidenceBoundary.PENDING_CONFIRMATION:
            raise ValueError("expert answers must remain pending_confirmation until audited")
        return self


class ExpertQuestion(AISchema):
    question_id: NonEmptyStr
    question: NonEmptyStr
    required_roles: list[NonEmptyStr] = Field(default_factory=list, max_length=3)
    sensitive: bool = False


class ResearchContextPackage(AISchema):
    research_task_id: NonEmptyStr
    conversation_summary: NonEmptyStr
    research_document_summary: NonEmptyStr
    profile_summary: NonEmptyStr
    evidence_snapshot: RetrievalSnapshot
    knowledge_gaps: list[NonEmptyStr] = Field(default_factory=list)
    questions: list[ExpertQuestion] = Field(default_factory=list, max_length=8)


class ContributionEvidence(AISchema):
    contribution_id: NonEmptyStr
    source_id: NonEmptyStr
    title: NonEmptyStr
    tags: list[NonEmptyStr] = Field(default_factory=list)
    reviewed: bool = False
    updated_at: datetime | None = None


class CandidateRecord(AISchema):
    candidate_id: NonEmptyStr
    display_name: NonEmptyStr
    contributions: list[ContributionEvidence] = Field(default_factory=list)


class CandidateRecommendation(AISchema):
    candidate_id: NonEmptyStr
    reason: NonEmptyStr
    contribution_ids: list[NonEmptyStr] = Field(min_length=1)


class DeepResearchStageResult(AISchema):
    trace_id: NonEmptyStr
    research_task_id: NonEmptyStr
    stage: ResearchStage
    status: ResearchTaskStatus
    research_plan: ResearchPlan | None = None
    findings: list[ResearchFinding] = Field(default_factory=list)
    routes: list[ResearchRoute] = Field(default_factory=list, max_length=3)
    audit: ResearchAudit | None = None
    expert_context: ResearchContextPackage | None = None
    final_solution: Solution | None = None

    @model_validator(mode="after")
    def final_stage_requires_solution(self) -> "DeepResearchStageResult":
        if self.stage == ResearchStage.FINAL_REPORT and self.final_solution is None:
            raise ValueError("final_report stage requires final_solution")
        return self
