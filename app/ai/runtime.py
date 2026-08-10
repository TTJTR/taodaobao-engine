import threading
from contextvars import ContextVar, Token
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
        self._records_by_trace: dict[str, AIRunMetadata] = {}

    def record(self, metadata: AIRunMetadata) -> None:
        self._records.append(metadata)
        self._records_by_trace[metadata.trace_id] = metadata

    def get(self, trace_id: str) -> AIRunMetadata | None:
        return self._records_by_trace.get(trace_id)

    @property
    def records(self) -> tuple[AIRunMetadata, ...]:
        return tuple(self._records)

    @property
    def last_run(self) -> AIRunMetadata | None:
        return self._records[-1] if self._records else None


class RunTimer:
    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or str(uuid4())
        self.started_at = datetime.now(UTC)
        self._started = perf_counter()

    @property
    def duration_ms(self) -> float:
        return (perf_counter() - self._started) * 1000


class TrustRunBudget:
    """One deadline and one HTTP-attempt budget shared by a trusted run."""

    def __init__(self, deadline_seconds: float, max_attempts: int) -> None:
        self.deadline_seconds = deadline_seconds
        self.max_attempts = max_attempts
        self._started = perf_counter()
        self._attempts = 0
        self._lock = threading.Lock()

    @property
    def attempts_used(self) -> int:
        with self._lock:
            return self._attempts

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_seconds - (perf_counter() - self._started))

    def consume_attempt(self) -> None:
        with self._lock:
            elapsed = perf_counter() - self._started
            if self.deadline_seconds - elapsed <= 0:
                raise TimeoutError("trusted solution deadline exceeded")
            if self._attempts >= self.max_attempts:
                raise RuntimeError("trusted solution retry budget exhausted")
            self._attempts += 1


ACTIVE_TRUST_BUDGET: ContextVar[TrustRunBudget | None] = ContextVar(
    "active_trust_budget",
    default=None,
)


def activate_trust_budget(budget: TrustRunBudget) -> Token[TrustRunBudget | None]:
    return ACTIVE_TRUST_BUDGET.set(budget)


def reset_trust_budget(token: Token[TrustRunBudget | None]) -> None:
    ACTIVE_TRUST_BUDGET.reset(token)
