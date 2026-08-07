import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ProfileStatus, ReviewStatus


class CustomerProfileData(BaseModel):
    industry: str | None = None
    background: str | None = None
    current_problem: str | None = None
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    existing_systems: list[str] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)


class CreateProfileRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)


class GenerateProfileRequest(BaseModel):
    source_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    supplemental_text: str | None = Field(default=None, max_length=20_000)


class UpdateProfileRequest(BaseModel):
    customer_name: str | None = Field(default=None, min_length=1, max_length=200)
    profile: CustomerProfileData
    source_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


class CustomerProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_name: str
    profile: dict
    source_ids: list[uuid.UUID]
    status: ProfileStatus
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class ExperienceData(BaseModel):
    name: str
    applicable_problem: str
    solution: str
    prerequisites: list[str] = Field(default_factory=list)
    result: str | None = None
    risks: list[str] = Field(default_factory=list)
    follow_up_foundation: str | None = None
    applicable_conditions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CapabilityData(BaseModel):
    name: str
    description: str
    inputs: list[str] = Field(min_length=1)
    outputs: list[str] = Field(min_length=1)
    prerequisites: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "reopen"]
    note: str | None = Field(default=None, max_length=1000)


class ExperienceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    data: dict
    review_status: ReviewStatus
    review_note: str | None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class CapabilityRead(ExperienceRead):
    pass

