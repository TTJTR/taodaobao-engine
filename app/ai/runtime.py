from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from uuid import uuid4

from pydantic import Field

from app.ai.schemas.base import AISchema, NonEmptyStr


class AIRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AIRunMetadata(AISchema):
    trace_id: NonEmptyStr
    method: NonEmptyStr
    stage: NonEmptyStr
    status: AIRunStatus
    model_version: NonEmptyStr
    prompt_version: NonEmptyStr
    schema_version: NonEmptyStr
    embedding_version: NonEmptyStr
    parameter_version: NonEmptyStr
    started_at: datetime
    duration_ms: float = Field(ge=0)
    structure_repair_count: int = Field(default=0, ge=0, le=1)
    model_attempt_count: int = Field(default=1, ge=0)
    error_code: str | None = None
    retryable: bool = False


class InMemoryRunRecorder:
    """Small audit sink; production can replace it with a database-backed recorder."""

    def __init__(self) -> None:
        self._records: list[AIRunMetadata] = []

    def record(self, metadata: AIRunMetadata) -> None:
        self._records.append(metadata)

    @property
    def records(self) -> tuple[AIRunMetadata, ...]:
        return tuple(self._records)

    @property
    def last_run(self) -> AIRunMetadata | None:
        return self._records[-1] if self._records else None

    def get_by_trace_id(self, trace_id: str) -> AIRunMetadata | None:
        return next(
            (record for record in reversed(self._records) if record.trace_id == trace_id),
            None,
        )


class RunTimer:
    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or str(uuid4())
        self.started_at = datetime.now(UTC)
        self._started = perf_counter()

    @property
    def duration_ms(self) -> float:
        return (perf_counter() - self._started) * 1000
