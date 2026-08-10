from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.ai.schemas.base import AISchema, NonEmptyStr
from app.ai.schemas.solution import EvidenceBoundary, Solution


class ClaimType(StrEnum):
    OWNER_ASSIGNMENT = "owner_assignment"
    CUSTOMER_FACT = "customer_fact"
    ENTERPRISE_CAPABILITY = "enterprise_capability"
    METRIC = "metric"
    TIME_BUDGET = "time_budget"
    COMPLIANCE = "compliance"
    HISTORICAL_RESULT = "historical_result"
    COMMITMENT = "commitment"
    RECOMMENDATION = "recommendation"
    PENDING_CONFIRMATION = "pending_confirmation"
    OTHER = "other"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationStatus(StrEnum):
    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    INVALID = "invalid"


class RecommendedAction(StrEnum):
    RELEASE = "release"
    DOWNGRADE = "downgrade"
    REVIEW = "review"
    BLOCK = "block"


class EvidenceLocation(AISchema):
    kind: Literal["field", "anchor", "character_range"]
    value: NonEmptyStr


class EvidenceRecord(AISchema):
    evidence_id: NonEmptyStr
    asset_id: NonEmptyStr
    source_id: NonEmptyStr
    source_version: NonEmptyStr
    reviewed_version: NonEmptyStr
    permission_snapshot_id: NonEmptyStr
    quote: NonEmptyStr
    location: EvidenceLocation
    title: NonEmptyStr
    url: NonEmptyStr | None = None
    author: NonEmptyStr | None = None
    source_updated_at: datetime | None = None
    last_synced_at: datetime | None = None
    permission_valid: bool
    available: bool
    invalid_reason: NonEmptyStr | None = None
    claim_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validity_and_links_must_be_consistent(self) -> "EvidenceRecord":
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("evidence claim_ids must be unique")
        if (
            not self.permission_valid
            or not self.available
            or self.source_version != self.reviewed_version
        ) and not self.invalid_reason:
            raise ValueError("invalid evidence requires invalid_reason")
        return self


class ClaimRecord(AISchema):
    claim_id: NonEmptyStr
    item_ref: NonEmptyStr
    text: NonEmptyStr
    claim_type: ClaimType
    boundary: EvidenceBoundary
    risk_level: RiskLevel
    section: NonEmptyStr
    evidence_refs: list[NonEmptyStr] = Field(default_factory=list)
    verification_status: VerificationStatus
    verification_reason: NonEmptyStr
    uncertainty_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def evidence_refs_must_be_unique(self) -> "ClaimRecord":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("claim evidence_refs must be unique")
        return self


class VerificationSummary(AISchema):
    entailed: int = Field(ge=0)
    contradicted: int = Field(ge=0)
    insufficient: int = Field(ge=0)
    invalid: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.entailed + self.contradicted + self.insufficient + self.invalid


class TrustQualityAttempt(AISchema):
    attempt: int = Field(ge=1, le=3)
    claims: list[ClaimRecord]
    evidence: list[EvidenceRecord]
    verification_summary: VerificationSummary
    recommended_action: RecommendedAction


class TrustedSolution(Solution):
    schema_version: Literal["solution-v2"] = "solution-v2"
    trace_id: NonEmptyStr
    claims: list[ClaimRecord]
    evidence: list[EvidenceRecord]
    verification_summary: VerificationSummary
    quality_attempts: list[TrustQualityAttempt] = Field(min_length=1, max_length=3)
    recommended_action: RecommendedAction

    @model_validator(mode="after")
    def trust_graph_must_be_consistent(self) -> "TrustedSolution":
        claim_ids = [claim.claim_id for claim in self.claims]
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("trusted solution claim_ids must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("trusted solution evidence_ids must be unique")

        claim_id_set = set(claim_ids)
        evidence_id_set = set(evidence_ids)
        for claim in self.claims:
            if not set(claim.evidence_refs).issubset(evidence_id_set):
                raise ValueError("claim references evidence outside the evidence bundle")
        for item in self.evidence:
            if not set(item.claim_ids).issubset(claim_id_set):
                raise ValueError("evidence references a claim outside the claim ledger")
            for claim_id in item.claim_ids:
                claim = next(record for record in self.claims if record.claim_id == claim_id)
                if item.evidence_id not in claim.evidence_refs:
                    raise ValueError("claim/evidence links must be bidirectional")

        expected_summary = VerificationSummary(
            entailed=sum(
                claim.verification_status == VerificationStatus.ENTAILED for claim in self.claims
            ),
            contradicted=sum(
                claim.verification_status == VerificationStatus.CONTRADICTED
                for claim in self.claims
            ),
            insufficient=sum(
                claim.verification_status == VerificationStatus.INSUFFICIENT
                for claim in self.claims
            ),
            invalid=sum(
                claim.verification_status == VerificationStatus.INVALID for claim in self.claims
            ),
        )
        if self.verification_summary != expected_summary:
            raise ValueError("verification_summary must match final claims")
        final_attempt = self.quality_attempts[-1]
        if final_attempt.claims != self.claims or final_attempt.evidence != self.evidence:
            raise ValueError("trusted solution must match the final quality attempt")
        if final_attempt.recommended_action != self.recommended_action:
            raise ValueError("recommended_action must match the final quality attempt")
        return self
