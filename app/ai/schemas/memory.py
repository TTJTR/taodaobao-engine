from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.ai.schemas.assets import CustomerProfileDraft
from app.ai.schemas.base import AISchema, NonEmptyStr


class MemoryOperation(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class MemorySuggestionStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


MemoryField = Literal[
    "industry",
    "background",
    "current_problems",
    "goals",
    "constraints",
    "existing_systems",
    "information_gaps",
]


class ProfileMemorySuggestion(AISchema):
    operation: MemoryOperation
    field: MemoryField
    proposed_value: NonEmptyStr
    previous_value: NonEmptyStr | None = None
    reason: NonEmptyStr
    source_ids: list[NonEmptyStr] = Field(min_length=1)
    source_quote: NonEmptyStr
    status: MemorySuggestionStatus = MemorySuggestionStatus.PENDING_CONFIRMATION


class ProfileMemoryProposal(AISchema):
    current_profile: CustomerProfileDraft
    suggestions: list[ProfileMemorySuggestion] = Field(default_factory=list)
    confirmation_required: bool = True
