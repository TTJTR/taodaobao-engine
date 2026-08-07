import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import MessageRole, ProcessStatus


class CreateSessionRequest(BaseModel):
    customer_profile_id: uuid.UUID
    title: str | None = Field(default=None, max_length=200)


class CreateTurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    mode: Literal["quick"]


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_profile_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    solution_run_id: uuid.UUID | None
    role: MessageRole
    content: str
    sequence: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class SolutionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    request_message_id: uuid.UUID
    status: ProcessStatus
    retrieval_snapshot: dict | None
    result: dict | None
    error_code: str | None
    retryable: bool
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    completed_at: datetime | None
