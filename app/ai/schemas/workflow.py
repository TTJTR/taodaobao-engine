from pydantic import Field, model_validator

from app.ai.schemas.base import AISchema
from app.ai.schemas.quality import QualityReport
from app.ai.schemas.solution import Solution


class QualityAttempt(AISchema):
    attempt: int = Field(ge=1, le=3)
    solution: Solution
    quality_report: QualityReport


class VerifiedSolutionResult(AISchema):
    solution: Solution
    quality_report: QualityReport
    attempts: list[QualityAttempt] = Field(min_length=1, max_length=3)
    revision_count: int = Field(ge=0, le=2)
    passed: bool

    @model_validator(mode="after")
    def final_status_must_match_attempts(self) -> "VerifiedSolutionResult":
        expected_attempt_numbers = list(range(1, len(self.attempts) + 1))
        actual_attempt_numbers = [attempt.attempt for attempt in self.attempts]
        if actual_attempt_numbers != expected_attempt_numbers:
            raise ValueError("quality attempts must be consecutive and start at 1")
        if self.revision_count != len(self.attempts) - 1:
            raise ValueError("revision_count must equal attempts minus one")
        final_attempt = self.attempts[-1]
        if self.solution != final_attempt.solution:
            raise ValueError("solution must match the final attempt")
        if self.quality_report != final_attempt.quality_report:
            raise ValueError("quality_report must match the final attempt")
        if self.passed != self.quality_report.passed:
            raise ValueError("passed must match the final quality report")
        if any(attempt.quality_report.passed for attempt in self.attempts[:-1]):
            raise ValueError("workflow must stop after the first passed quality report")
        return self
