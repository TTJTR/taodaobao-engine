from datetime import UTC, datetime

from app.ai.schemas import RetrievalSnapshot, SolutionContext
from app.services.ai_payload_adapter import (
    build_solution_context,
    normalize_retrieval_snapshot,
    normalize_solution_v2_result,
)


def test_adapter_converts_backend_payloads_to_ai_contracts() -> None:
    context = build_solution_context(
        "视觉质检",
        {
            "id": "profile-1",
            "customer_name": "制造客户",
            "status": "confirmed",
            "profile": {"industry": "制造", "background": "产线升级"},
        },
    )
    snapshot = normalize_retrieval_snapshot(
        {
            "experiences": [
                {
                    "id": "experience-1",
                    "source_id": "source-1",
                    "data": {
                        "name": "质检试点",
                        "problem": "人工复检压力",
                        "solution": "旁路部署",
                        "prerequisites": ["摄像设备", "缺陷样本"],
                        "risks": ["现场光照变化"],
                    },
                    "source_snapshot": {
                        "source_version": "3",
                        "reviewed_version": "3",
                        "permission_snapshot_id": "source-1:3:checked",
                        "permission_valid": True,
                        "available": True,
                    },
                }
            ],
            "capabilities": [
                {
                    "id": "capability-1",
                    "source_id": "source-2",
                    "data": {
                        "name": "缺陷识别",
                        "description": "识别产品缺陷",
                        "inputs": ["摄像数据"],
                        "outputs": ["缺陷结果"],
                        "limitations": ["需要现场校准", "依赖样本质量"],
                    },
                }
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    validated_context = SolutionContext.model_validate(context)
    validated_snapshot = RetrievalSnapshot.model_validate(snapshot)

    assert validated_context.current_requirement == "视觉质检"
    assert validated_context.customer_profile.source_ids == ["profile-snapshot:profile-1"]
    assert validated_snapshot.experiences[0].rank == 1
    assert validated_snapshot.experiences[0].data.applicable_problem == "人工复检压力"
    assert validated_snapshot.experiences[0].data.prerequisites == "摄像设备；缺陷样本"
    assert snapshot["experiences"][0]["source_snapshot"]["reviewed_version"] == "3"
    assert validated_snapshot.capabilities[0].data.source_id == "source-2"
    assert validated_snapshot.capabilities[0].data.limitations == "需要现场校准；依赖样本质量"


def test_adapter_converts_molly_trusted_solution_to_backend_gate_payload() -> None:
    claim = {
        "claim_id": "clm-1",
        "item_ref": "historical_evidence:1",
        "text": "已完成视觉质检试点。",
        "claim_type": "historical_result",
        "boundary": "historical_fact",
        "risk_level": "high",
        "section": "historical_evidence",
        "evidence_refs": ["ev-1"],
        "verification_status": "entailed",
        "verification_reason": "原文直接支持",
        "uncertainty_score": None,
    }
    evidence = {
        "evidence_id": "ev-1",
        "asset_type": "experience",
        "asset_id": "11111111-1111-1111-1111-111111111111",
        "source_id": "22222222-2222-2222-2222-222222222222",
        "source_version": "3",
        "reviewed_version": "3",
        "permission_snapshot_id": "source:3:checked",
        "quote": "已完成视觉质检试点。",
        "location": {"kind": "field", "value": "source_quote"},
        "title": "视觉质检复盘",
        "url": None,
        "author": None,
        "source_updated_at": None,
        "last_synced_at": None,
        "permission_valid": True,
        "available": True,
        "invalid_reason": None,
        "claim_ids": ["clm-1"],
    }
    candidate = {
        "requirement_understanding": [],
        "initial_recommendations": [],
        "historical_evidence": [
            {
                "text": "已完成视觉质检试点。",
                "boundary": "historical_fact",
                "asset_id": evidence["asset_id"],
                "source_id": evidence["source_id"],
            }
        ],
        "capability_composition": [],
        "prerequisites_and_risks": [],
        "pending_confirmations": [],
        "sources": [
            {
                "asset_id": evidence["asset_id"],
                "source_id": evidence["source_id"],
                "title": "视觉质检复盘",
                "url": None,
            }
        ],
        "suggested_questions": [],
        "schema_version": "solution-v2",
        "trace_id": "trace-live-contract",
        "claims": [claim],
        "evidence": [evidence],
        "verification_summary": {
            "entailed": 1,
            "contradicted": 0,
            "insufficient": 0,
            "invalid": 0,
        },
        "quality_attempts": [
            {
                "attempt": 1,
                "claims": [claim],
                "evidence": [evidence],
                "verification_summary": {
                    "entailed": 1,
                    "contradicted": 0,
                    "insufficient": 0,
                    "invalid": 0,
                },
                "recommended_action": "release",
            }
        ],
        "recommended_action": "release",
    }

    payload = normalize_solution_v2_result(candidate)

    assert payload["solution"]["historical_evidence"][0]["text"] == claim["text"]
    assert payload["evidence"][0]["evidence_key"] == "ev-1"
    assert payload["claim_evidence_links"][0]["claim_id"] == "clm-1"
    assert payload["recommended_action"] == "release"
