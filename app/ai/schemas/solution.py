from enum import StrEnum

from pydantic import Field, model_validator

from app.ai.schemas.base import AISchema, NonEmptyStr


class EvidenceBoundary(StrEnum):
    HISTORICAL_FACT = "historical_fact"
    ENTERPRISE_CAPABILITY = "enterprise_capability"
    AI_INFERENCE = "ai_inference"
    PENDING_CONFIRMATION = "pending_confirmation"


class CitedItem(AISchema):
    text: NonEmptyStr
    boundary: EvidenceBoundary
    asset_id: NonEmptyStr | None = None
    source_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def facts_and_capabilities_need_sources(self) -> "CitedItem":
        sourced_boundaries = {
            EvidenceBoundary.HISTORICAL_FACT,
            EvidenceBoundary.ENTERPRISE_CAPABILITY,
        }
        if self.boundary in sourced_boundaries and not (self.asset_id and self.source_id):
            raise ValueError(
                "historical facts and enterprise capabilities require asset_id and source_id"
            )
        if self.boundary not in sourced_boundaries and (self.asset_id or self.source_id):
            raise ValueError(
                "inferences and pending confirmations must not cite enterprise sources"
            )
        return self


class SourceReference(AISchema):
    asset_id: NonEmptyStr
    source_id: NonEmptyStr
    title: NonEmptyStr
    url: NonEmptyStr | None = None


class Solution(AISchema):
    requirement_understanding: list[CitedItem] = Field(default_factory=list)
    initial_recommendations: list[CitedItem] = Field(default_factory=list)
    historical_evidence: list[CitedItem] = Field(default_factory=list)
    capability_composition: list[CitedItem] = Field(default_factory=list)
    prerequisites_and_risks: list[CitedItem] = Field(default_factory=list)
    pending_confirmations: list[CitedItem] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    suggested_questions: list[NonEmptyStr] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def cited_sources_must_appear_in_sources(self) -> "Solution":
        source_pairs = {(source.asset_id, source.source_id) for source in self.sources}
        if len(source_pairs) != len(self.sources):
            raise ValueError("sources must be unique")
        sections = (
            self.requirement_understanding,
            self.initial_recommendations,
            self.historical_evidence,
            self.capability_composition,
            self.prerequisites_and_risks,
            self.pending_confirmations,
        )
        missing_pairs = {
            (item.asset_id, item.source_id)
            for section in sections
            for item in section
            if item.asset_id
            and item.source_id
            and (item.asset_id, item.source_id) not in source_pairs
        }
        if missing_pairs:
            missing = ", ".join(f"{asset_id}/{source_id}" for asset_id, source_id in missing_pairs)
            raise ValueError(f"cited sources are missing from sources: {missing}")
        return self
