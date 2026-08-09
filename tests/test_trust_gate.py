import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.engine import MockAIEngine
from app.db.models import SourceFreshness, SourceStatus, TrustAction
from app.schemas.trust import SolutionV2Payload
from app.services.solution_trust_service import _evidence_still_visible
from app.services.trust_gate import EvidenceState, evaluate_trust_gate


def _payload(*, label: str = "entailed", risk: str = "high") -> SolutionV2Payload:
    return SolutionV2Payload.model_validate(
        {
            "schema_version": "solution-v2",
            "solution": {
                "requirement_understanding": [],
                "initial_recommendations": [],
                "historical_evidence": [
                    {
                        "text": "历史项目完成了旁路质检",
                        "boundary": "historical_fact",
                        "asset_id": "00000000-0000-4000-8000-000000000010",
                        "source_id": "00000000-0000-4000-8000-000000000020",
                    }
                ],
                "capability_composition": [],
                "prerequisites_and_risks": [],
                "pending_confirmations": [],
                "sources": [
                    {
                        "asset_id": "00000000-0000-4000-8000-000000000010",
                        "source_id": "00000000-0000-4000-8000-000000000020",
                        "title": "历史经验",
                    }
                ],
                "suggested_questions": [],
            },
            "claims": [
                {
                    "claim_id": "historical_evidence:1",
                    "section": "historical_evidence",
                    "text": "历史项目完成了旁路质检",
                    "claim_type": "historical_fact",
                    "boundary": "historical_fact",
                    "risk_level": risk,
                    "verification_status": label,
                    "evidence_refs": ["asset/source"],
                }
            ],
            "evidence": [
                {"evidence_key": "asset/source", "asset_id": "asset", "source_id": "source"}
            ],
            "claim_evidence_links": [
                {
                    "claim_id": "historical_evidence:1",
                    "evidence_key": "asset/source",
                    "label": label,
                    "score": None,
                    "verifier_version": "quality-zh-v1",
                }
            ],
            "quality_attempts": [
                {
                    "attempt": 1,
                    "candidate_version": 1,
                    "failed_claim_ids": [] if label == "entailed" else ["historical_evidence:1"],
                    "quality_report": {},
                    "duration_ms": 1,
                }
            ],
            "verifier_version": "quality-zh-v1",
            "recommended_action": "release",
        }
    )


def _state(**updates) -> dict[str, EvidenceState]:
    values = {
        "evidence_key": "asset/source",
        "in_snapshot": True,
        "location_reproducible": True,
        "reviewed_version_current": True,
        "permission_valid": True,
    }
    values.update(updates)
    return {"asset/source": EvidenceState(**values)}


def test_gate_releases_only_when_all_hard_rules_pass() -> None:
    result = evaluate_trust_gate(_payload(), _state())
    assert result.action == TrustAction.RELEASE
    assert result.released_claim_ids == {"historical_evidence:1"}


def test_gate_downgrades_unverified_high_risk_claim_even_if_ai_recommends_release() -> None:
    result = evaluate_trust_gate(_payload(label="insufficient"), _state())
    assert result.action == TrustAction.DOWNGRADE
    assert "TG06_HIGH_RISK_NOT_ENTAILED" in result.reason_codes
    assert not result.released_claim_ids


def test_gate_blocks_permission_revocation_and_contradicted_high_risk_claim() -> None:
    permission = evaluate_trust_gate(_payload(), _state(permission_valid=False))
    contradiction = evaluate_trust_gate(_payload(label="contradicted"), _state())
    assert permission.action == TrustAction.BLOCK
    assert contradiction.action == TrustAction.BLOCK


def test_gate_sends_stale_source_to_review() -> None:
    result = evaluate_trust_gate(_payload(), _state(reviewed_version_current=False))
    assert result.action == TrustAction.REVIEW
    assert not result.released_claim_ids


def test_current_permission_and_version_are_rechecked_before_evidence_is_shown() -> None:
    source_id = uuid.uuid4()
    evidence = SimpleNamespace(
        source_id=source_id,
        permission_status="granted",
        source_version=1,
        reviewed_source_version=1,
    )
    source = SimpleNamespace(
        id=source_id,
        status=SourceStatus.COMPLETED,
        freshness_status=SourceFreshness.CURRENT,
        content_version=1,
    )
    assert _evidence_still_visible(evidence, {source_id: source})
    source.freshness_status = SourceFreshness.PERMISSION_DENIED
    assert not _evidence_still_visible(evidence, {source_id: source})
    source.freshness_status = SourceFreshness.CURRENT
    source.content_version = 2
    assert not _evidence_still_visible(evidence, {source_id: source})


def test_solution_v2_rejects_missing_claim_ledger() -> None:
    data = _payload().model_dump(mode="json")
    data["claims"] = []
    with pytest.raises(ValidationError):
        SolutionV2Payload.model_validate(data)


@pytest.mark.asyncio
async def test_mock_engine_supports_solution_v2_through_the_frozen_method() -> None:
    context = {
        "schema_version": "solution-v2",
        "trace_id": "trace-test-v2",
        "customer_profile": {
            "customer_name": "测试客户",
            "profile_status": "confirmed",
            "profile_summary": "测试画像",
            "source_ids": ["profile-source"],
        },
        "current_requirement": "需要一份可信方案",
    }
    snapshot = {
        "experiences": [],
        "capabilities": [],
        "conflicts": [],
        "missing_information": [],
        "gap_summary": None,
        "can_generate_solution": False,
        "created_at": datetime.now(UTC).isoformat(),
    }
    result = await MockAIEngine().generate_solution(context, snapshot)
    parsed = SolutionV2Payload.model_validate(result)
    assert parsed.schema_version == "solution-v2"
    assert parsed.claims
    assert parsed.quality_attempts[0].attempt == 1
