import json
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from app.ai.schemas.base import AISchema, NonEmptyStr


class TrustExpectedStatus(StrEnum):
    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    INVALID = "invalid"


class TrustClaimCase(AISchema):
    case_id: NonEmptyStr
    claim: NonEmptyStr
    type: NonEmptyStr
    risk: NonEmptyStr
    evidence: list[NonEmptyStr] = Field(default_factory=list)
    source_valid: bool = True
    expected: TrustExpectedStatus


class TrustClaimSuite(AISchema):
    suite_version: NonEmptyStr
    schema_version: NonEmptyStr
    cases: list[TrustClaimCase] = Field(min_length=30)


def load_trust_claim_suite(path: Path) -> TrustClaimSuite:
    return TrustClaimSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))


def trust_suite_coverage(suite: TrustClaimSuite) -> dict[str, int]:
    counts = {status.value: 0 for status in TrustExpectedStatus}
    for case in suite.cases:
        counts[case.expected.value] += 1
    return counts
