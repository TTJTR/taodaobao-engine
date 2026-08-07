import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.db.models import JobType, ProcessStatus, SourcePurpose, SourceStatus, SourceType


class ImportLinkRequest(BaseModel):
    url: HttpUrl
    purpose: SourcePurpose
    customer_profile_id: uuid.UUID | None = None


class ImportTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)
    content_type: Literal["plain_text", "chat_record", "meeting_transcript"]
    purpose: SourcePurpose
    customer_profile_id: uuid.UUID | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_profile_id: uuid.UUID | None
    type: SourceType
    purpose: SourcePurpose
    title: str
    content: str | None
    source_url: str | None
    author: str | None
    source_updated_at: datetime | None
    synced_at: datetime | None
    tags: list[str]
    status: SourceStatus
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    target_id: uuid.UUID
    status: ProcessStatus
    stage: str
    error_code: str | None
    error_summary: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    finished_at: datetime | None
