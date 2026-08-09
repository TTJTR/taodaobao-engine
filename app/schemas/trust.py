from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.schemas.solution import Solution


class SolutionV2Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=160)
    section: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=20_000)
    claim_type: str = Field(min_length=1, max_length=64)
    boundary: Literal[
        "historical_fact",
        "enterprise_capability",
        "ai_inference",
        "pending_confirmation",
    ]
    risk_level: Literal["low", "medium", "high"]
    verification_status: Literal["entailed", "contradicted", "insufficient", "invalid"]
    evidence_refs: list[str] = Field(default_factory=list)


class SolutionV2EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_key: str = Field(min_length=3, max_length=256)
    asset_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)


class SolutionV2Link(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    evidence_key: str
    label: Literal["entailed", "contradicted", "insufficient", "invalid"]
    score: float | None = Field(default=None, ge=0, le=1)
    verifier_version: str = Field(min_length=1, max_length=128)


class SolutionV2QualityAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1, le=3)
    candidate_version: int = Field(ge=1)
    failed_claim_ids: list[str] = Field(default_factory=list)
    quality_report: dict
    duration_ms: int | None = Field(default=None, ge=0)


class SolutionV2Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["solution-v2"]
    solution: Solution
    claims: list[SolutionV2Claim] = Field(min_length=1)
    evidence: list[SolutionV2EvidenceRef] = Field(default_factory=list)
    claim_evidence_links: list[SolutionV2Link] = Field(default_factory=list)
    quality_attempts: list[SolutionV2QualityAttempt] = Field(min_length=1, max_length=3)
    verifier_version: str = Field(min_length=1, max_length=128)
    recommended_action: Literal["release", "downgrade", "review", "block"]

    @model_validator(mode="after")
    def ledger_references_must_be_closed(self) -> "SolutionV2Payload":
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_ids must be unique")
        evidence_keys = [item.evidence_key for item in self.evidence]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("evidence_keys must be unique")
        allowed_claims = set(claim_ids)
        allowed_evidence = set(evidence_keys)
        for claim in self.claims:
            if not set(claim.evidence_refs).issubset(allowed_evidence):
                raise ValueError("claim references evidence outside the evidence ledger")
            if claim.boundary in {"historical_fact", "enterprise_capability"} and not (
                claim.evidence_refs
            ):
                raise ValueError("enterprise claims require evidence")
        for link in self.claim_evidence_links:
            if link.claim_id not in allowed_claims or link.evidence_key not in allowed_evidence:
                raise ValueError("claim-evidence link points outside the ledgers")
        return self


class TrustReviewRequest(BaseModel):
    expected_decision_version: int = Field(ge=1)
    decision: Literal["approve", "downgrade", "reject", "revise"]
    reason: str = Field(min_length=3, max_length=4000)
    edits: dict = Field(default_factory=dict)
    evidence_changes: list[dict] = Field(default_factory=list, max_length=100)
