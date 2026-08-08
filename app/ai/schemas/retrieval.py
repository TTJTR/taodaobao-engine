from datetime import datetime

from pydantic import Field, model_validator

from app.ai.schemas.assets import CapabilityDraft, ExperienceDraft
from app.ai.schemas.base import AISchema, NonEmptyStr


class RetrievedExperience(AISchema):
    asset_id: NonEmptyStr
    source_id: NonEmptyStr
    rank: int = Field(ge=1)
    match_reasons: list[NonEmptyStr] = Field(min_length=1)
    data: ExperienceDraft

    @model_validator(mode="after")
    def source_must_match_data(self) -> "RetrievedExperience":
        if self.source_id != self.data.source_id:
            raise ValueError("source_id must match data.source_id")
        return self


class RetrievedCapability(AISchema):
    asset_id: NonEmptyStr
    source_id: NonEmptyStr
    rank: int = Field(ge=1)
    match_reasons: list[NonEmptyStr] = Field(min_length=1)
    data: CapabilityDraft

    @model_validator(mode="after")
    def source_must_match_data(self) -> "RetrievedCapability":
        if self.source_id != self.data.source_id:
            raise ValueError("source_id must match data.source_id")
        return self


class EvidenceConflict(AISchema):
    description: NonEmptyStr
    asset_ids: list[NonEmptyStr] = Field(min_length=2)
    source_ids: list[NonEmptyStr] = Field(min_length=2)
    clarification_question: NonEmptyStr


class RetrievalSnapshot(AISchema):
    experiences: list[RetrievedExperience] = Field(default_factory=list, max_length=3)
    capabilities: list[RetrievedCapability] = Field(default_factory=list, max_length=5)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    missing_information: list[NonEmptyStr] = Field(default_factory=list)
    gap_summary: NonEmptyStr | None = None
    can_generate_solution: bool = True
    created_at: datetime

    @model_validator(mode="after")
    def asset_ids_and_ranks_must_be_unique(self) -> "RetrievalSnapshot":
        for items, label in (
            (self.experiences, "experiences"),
            (self.capabilities, "capabilities"),
        ):
            asset_ids = [item.asset_id for item in items]
            ranks = [item.rank for item in items]
            if len(asset_ids) != len(set(asset_ids)):
                raise ValueError(f"{label} asset_ids must be unique")
            if len(ranks) != len(set(ranks)):
                raise ValueError(f"{label} ranks must be unique")
        available_asset_ids = {item.asset_id for item in [*self.experiences, *self.capabilities]}
        available_source_ids = {item.source_id for item in [*self.experiences, *self.capabilities]}
        for conflict in self.conflicts:
            if len(conflict.asset_ids) != len(set(conflict.asset_ids)):
                raise ValueError("conflict asset_ids must be unique")
            if not set(conflict.asset_ids).issubset(available_asset_ids):
                raise ValueError("conflict contains an asset outside the retrieval snapshot")
            if not set(conflict.source_ids).issubset(available_source_ids):
                raise ValueError("conflict contains a source outside the retrieval snapshot")
        return self
