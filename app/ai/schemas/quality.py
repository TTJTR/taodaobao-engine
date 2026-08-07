from enum import StrEnum

from pydantic import Field, model_validator

from app.ai.schemas.base import AISchema, NonEmptyStr


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NOT_DOCUMENTED = "not_documented"


class ClaimReview(AISchema):
    claim_id: NonEmptyStr
    verdict: ClaimVerdict
    reason: NonEmptyStr


class QualityReport(AISchema):
    reviews: list[ClaimReview] = Field(min_length=1)
    passed: bool
    requires_regeneration: bool
    failed_claim_ids: list[NonEmptyStr] = Field(default_factory=list)
    summary: NonEmptyStr

    @model_validator(mode="after")
    def status_must_match_reviews(self) -> "QualityReport":
        claim_ids = [review.claim_id for review in self.reviews]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("quality review claim_ids must be unique")

        expected_failed_ids = [
            review.claim_id for review in self.reviews if review.verdict != ClaimVerdict.SUPPORTED
        ]
        if self.failed_claim_ids != expected_failed_ids:
            raise ValueError("failed_claim_ids must match failed reviews in review order")
        if self.passed != (not expected_failed_ids):
            raise ValueError("passed must be true only when every claim is supported")
        if self.requires_regeneration == self.passed:
            raise ValueError("requires_regeneration must be the opposite of passed")
        return self
