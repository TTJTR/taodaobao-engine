from typing import Any

from app.ai.schemas import Solution, TrustedSolution
from app.schemas.trust import SolutionV2Payload

_SOLUTION_FIELDS = (
    "requirement_understanding",
    "initial_recommendations",
    "historical_evidence",
    "capability_composition",
    "prerequisites_and_risks",
    "pending_confirmations",
    "sources",
    "suggested_questions",
)
_TRUST_VERIFIER_VERSION = "molly-trusted-solution-v2"


def build_solution_context(requirement: str, profile_snapshot: dict[str, Any]) -> dict:
    profile_data = profile_snapshot.get("profile") or {}
    customer_name = str(
        profile_snapshot.get("customer_name") or profile_data.get("customer_name") or "待确认客户"
    )
    source_ids = profile_data.get("source_ids") or profile_snapshot.get("source_ids") or []
    if not source_ids:
        snapshot_id = profile_snapshot.get("id", "unknown")
        source_ids = [f"profile-snapshot:{snapshot_id}"]
    summary = str(
        profile_data.get("profile_summary")
        or profile_data.get("background")
        or f"{customer_name}的客户画像，细节仍需在会话中确认。"
    )
    return {
        "customer_profile": {
            "customer_name": customer_name,
            "industry": profile_data.get("industry"),
            "background": profile_data.get("background"),
            "current_problems": _string_list(
                profile_data.get("current_problems") or profile_data.get("current_problem")
            ),
            "goals": _string_list(profile_data.get("goals")),
            "constraints": _string_list(profile_data.get("constraints")),
            "existing_systems": _string_list(profile_data.get("existing_systems")),
            "information_gaps": _string_list(profile_data.get("information_gaps")),
            "profile_status": profile_snapshot.get("status", "pending_confirmation"),
            "profile_summary": summary,
            "source_ids": [str(item) for item in source_ids],
        },
        "current_requirement": requirement,
    }


def normalize_retrieval_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiences": [
            _normalize_experience(item, rank)
            for rank, item in enumerate(snapshot.get("experiences", []), start=1)
        ],
        "capabilities": [
            _normalize_capability(item, rank)
            for rank, item in enumerate(snapshot.get("capabilities", []), start=1)
        ],
        "conflicts": snapshot.get("conflicts", []),
        "missing_information": snapshot.get("missing_information", []),
        "gap_summary": snapshot.get("gap_summary"),
        "can_generate_solution": snapshot.get("can_generate_solution", True),
        "created_at": snapshot["created_at"],
    }


def normalize_solution_v2_result(candidate: dict[str, Any]) -> dict[str, Any]:
    """Adapt the frozen AI method's V2 result to the backend persistence contract.

    The backend also accepts its earlier nested V2 payload so offline Mock and stored
    fixtures remain compatible. Live Molly output is validated as ``TrustedSolution``
    before it is converted; this is an explicit boundary adapter, not field guessing.
    """

    try:
        return SolutionV2Payload.model_validate(candidate).model_dump(mode="json")
    except ValueError:
        trusted = TrustedSolution.model_validate(candidate)

    trusted_data = trusted.model_dump(mode="json")
    solution = Solution.model_validate(
        {field: trusted_data[field] for field in _SOLUTION_FIELDS}
    ).model_dump(mode="json")
    claims = [
        {
            "claim_id": claim.claim_id,
            "section": claim.section,
            "text": claim.text,
            "claim_type": claim.claim_type.value,
            "boundary": claim.boundary.value,
            "risk_level": claim.risk_level.value,
            "verification_status": claim.verification_status.value,
            "evidence_refs": list(claim.evidence_refs),
        }
        for claim in trusted.claims
    ]
    evidence = [
        {
            "evidence_key": item.evidence_id,
            "asset_id": item.asset_id,
            "source_id": item.source_id,
            "source_version": item.source_version,
            "reviewed_version": item.reviewed_version,
            "permission_snapshot_id": item.permission_snapshot_id,
            "quote": item.quote,
            "location": item.location.model_dump(mode="json"),
            "title": item.title,
            "url": item.url,
            "author": item.author,
            "source_updated_at": item.source_updated_at,
            "last_synced_at": item.last_synced_at,
            "permission_valid": item.permission_valid,
            "available": item.available,
            "invalid_reason": item.invalid_reason,
        }
        for item in trusted.evidence
    ]
    links = [
        {
            "claim_id": claim.claim_id,
            "evidence_key": evidence_key,
            "label": claim.verification_status.value,
            "score": None,
            "verifier_version": _TRUST_VERIFIER_VERSION,
        }
        for claim in trusted.claims
        for evidence_key in claim.evidence_refs
    ]
    attempts = []
    for attempt in trusted.quality_attempts:
        attempt_data = attempt.model_dump(mode="json")
        attempts.append(
            {
                "attempt": attempt.attempt,
                "candidate_version": attempt.attempt,
                "failed_claim_ids": [
                    claim.claim_id
                    for claim in attempt.claims
                    if claim.verification_status.value != "entailed"
                ],
                "quality_report": attempt_data,
                "duration_ms": None,
            }
        )
    payload = {
        "schema_version": "solution-v2",
        "solution": solution,
        "claims": claims,
        "evidence": evidence,
        "claim_evidence_links": links,
        "quality_attempts": attempts,
        "verifier_version": _TRUST_VERIFIER_VERSION,
        "recommended_action": trusted.recommended_action.value,
    }
    return SolutionV2Payload.model_validate(payload).model_dump(mode="json")


def _normalize_experience(item: dict[str, Any], rank: int) -> dict[str, Any]:
    data = dict(item.get("data") or {})
    source_id = str(item["source_id"])
    name = str(data.get("name") or data.get("title") or "已审核经验")
    applicable_problem = str(data.get("applicable_problem") or data.get("problem") or name)
    data.pop("problem", None)
    data.update(
        {
            "name": name,
            "applicable_problem": applicable_problem,
            "solution": str(data.get("solution") or data.get("description") or name),
            "source_id": source_id,
        }
    )
    for field in ("prerequisites", "risks", "applicable_conditions"):
        if field in data:
            data[field] = _optional_text(data[field])
    return {
        "asset_id": str(item.get("asset_id") or item["id"]),
        "source_id": source_id,
        "rank": rank,
        "match_reasons": item.get("match_reasons") or ["关键词匹配与更新时间排序"],
        "data": data,
        "source_snapshot": item.get("source_snapshot"),
    }


def _normalize_capability(item: dict[str, Any], rank: int) -> dict[str, Any]:
    data = dict(item.get("data") or {})
    source_id = str(item["source_id"])
    name = str(data.get("name") or data.get("title") or "已审核能力")
    data.update(
        {
            "name": name,
            "description": str(data.get("description") or data.get("solution") or name),
            "source_id": source_id,
        }
    )
    for field in ("prerequisites", "limitations"):
        if field in data:
            data[field] = _optional_text(data[field])
    return {
        "asset_id": str(item.get("asset_id") or item["id"]),
        "source_id": source_id,
        "rank": rank,
        "match_reasons": item.get("match_reasons") or ["关键词匹配与更新时间排序"],
        "data": data,
        "source_snapshot": item.get("source_snapshot"),
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts) or None
    text = str(value).strip()
    return text or None
