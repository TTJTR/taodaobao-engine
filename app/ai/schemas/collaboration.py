from typing import Literal

from pydantic import Field

from app.ai.schemas.base import AISchema, NonEmptyStr
from app.ai.schemas.solution import EvidenceBoundary, SourceReference


class CollaborationDocumentItem(AISchema):
    text: NonEmptyStr
    boundary: EvidenceBoundary | None = None
    asset_id: NonEmptyStr | None = None
    source_id: NonEmptyStr | None = None


class CollaborationDocumentSection(AISchema):
    key: NonEmptyStr
    title: NonEmptyStr
    items: list[CollaborationDocumentItem] = Field(default_factory=list)


class FeishuDocumentDraft(AISchema):
    research_task_id: NonEmptyStr
    title: NonEmptyStr
    sections: list[CollaborationDocumentSection] = Field(min_length=8)
    sources: list[SourceReference] = Field(default_factory=list)


class FeishuCardDraft(AISchema):
    research_task_id: NonEmptyStr
    title: NonEmptyStr
    conclusion: str = Field(min_length=1, max_length=160)
    key_points: list[NonEmptyStr] = Field(default_factory=list, max_length=3)
    status: Literal["ready_for_review", "awaiting_expert"]
    document_url: NonEmptyStr
    action_label: NonEmptyStr = "查看完整研究并参与协作"


class FeishuGroupOpeningDraft(AISchema):
    research_task_id: NonEmptyStr
    text: str = Field(min_length=1, max_length=1500)
    candidate_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=3)
    question_ids: list[NonEmptyStr] = Field(default_factory=list, max_length=8)
    document_url: NonEmptyStr


class CollaborationContentBundle(AISchema):
    document: FeishuDocumentDraft
    card: FeishuCardDraft
    group_opening: FeishuGroupOpeningDraft


class ExpertReplySummary(AISchema):
    research_task_id: NonEmptyStr
    question_id: NonEmptyStr
    author_id: NonEmptyStr
    author_name: NonEmptyStr
    message_url: NonEmptyStr
    summary: NonEmptyStr
    boundary: EvidenceBoundary = EvidenceBoundary.PENDING_CONFIRMATION
