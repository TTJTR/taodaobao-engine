from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, Field, field_validator

from app.ai.schemas.base import AISchema, NonEmptyStr


class ProfileStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"


class SpeakerRole(StrEnum):
    CUSTOMER = "customer"
    SALES = "sales"
    UNKNOWN = "unknown"


PROFILE_FACT_FIELDS = Literal[
    "industry",
    "background",
    "current_problems",
    "goals",
    "constraints",
    "existing_systems",
    "information_gaps",
]


class ProfileFactSource(AISchema):
    field: PROFILE_FACT_FIELDS
    value: NonEmptyStr
    source_id: NonEmptyStr
    quote: NonEmptyStr
    speaker_role: SpeakerRole = SpeakerRole.UNKNOWN


class ProfileConflict(AISchema):
    field: PROFILE_FACT_FIELDS
    conflicting_values: list[NonEmptyStr] = Field(min_length=2)
    source_ids: list[NonEmptyStr] = Field(min_length=2)
    clarification_question: NonEmptyStr


class CustomerProfileDraft(AISchema):
    customer_name: NonEmptyStr
    industry: NonEmptyStr | None = None
    background: NonEmptyStr | None = None
    current_problems: list[NonEmptyStr] = Field(default_factory=list)
    goals: list[NonEmptyStr] = Field(default_factory=list)
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    existing_systems: list[NonEmptyStr] = Field(default_factory=list)
    information_gaps: list[NonEmptyStr] = Field(default_factory=list)
    profile_status: ProfileStatus = ProfileStatus.PENDING_CONFIRMATION
    profile_summary: NonEmptyStr
    source_ids: list[NonEmptyStr] = Field(min_length=1, max_length=20)
    fact_sources: list[ProfileFactSource] = Field(default_factory=list)
    conflicts: list[ProfileConflict] = Field(default_factory=list)

    @field_validator("source_ids")
    @classmethod
    def source_ids_must_be_unique(cls, source_ids: list[str]) -> list[str]:
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be unique")
        return source_ids


class ExperienceDraft(AISchema):
    name: NonEmptyStr
    applicable_problem: NonEmptyStr = Field(
        validation_alias=AliasChoices("applicable_problem", "problem")
    )
    solution: NonEmptyStr
    prerequisites: NonEmptyStr | None = None
    result: NonEmptyStr | None = None
    risks: NonEmptyStr | None = None
    follow_up_foundation: NonEmptyStr | None = None
    applicable_conditions: NonEmptyStr | None = None
    tags: list[NonEmptyStr] = Field(default_factory=list)
    source_id: NonEmptyStr
    source_quote: NonEmptyStr | None = None
    source_anchor: NonEmptyStr | None = None
    evidence_status: Literal["historical_record", "concept_only", "unclear"] = "unclear"
    embedding_text: NonEmptyStr | None = None

    @property
    def problem(self) -> str:
        """Backward-compatible Python accessor; serialized field is applicable_problem."""
        return self.applicable_problem


class CapabilityDraft(AISchema):
    name: NonEmptyStr
    description: NonEmptyStr
    inputs: list[NonEmptyStr] = Field(
        default_factory=list,
        validation_alias=AliasChoices("inputs", "input"),
    )
    outputs: list[NonEmptyStr] = Field(
        default_factory=list,
        validation_alias=AliasChoices("outputs", "output"),
    )
    prerequisites: NonEmptyStr | None = None
    limitations: NonEmptyStr | None = None
    dependencies: list[NonEmptyStr] = Field(default_factory=list)
    tags: list[NonEmptyStr] = Field(default_factory=list)
    source_id: NonEmptyStr
    source_quote: NonEmptyStr | None = None
    source_anchor: NonEmptyStr | None = None
    review_warnings: list[NonEmptyStr] = Field(default_factory=list)
    merge_suggestion: NonEmptyStr | None = None
    embedding_text: NonEmptyStr | None = None

    @field_validator("inputs", "outputs", mode="before")
    @classmethod
    def legacy_scalar_becomes_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value

    @property
    def input(self) -> str:
        """Backward-compatible accessor for older prompt builders and fixtures."""
        return "；".join(self.inputs)

    @property
    def output(self) -> str:
        return "；".join(self.outputs)
