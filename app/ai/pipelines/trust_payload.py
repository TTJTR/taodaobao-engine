import re
from typing import Any

from app.ai.pipelines.quality import build_claim_packets
from app.ai.schemas import RetrievalSnapshot, SolutionContext, VerifiedSolutionResult

_HIGH_RISK_PATTERN = re.compile(
    r"(?:\d|负责人|金额|预算|周期|日期|上线|交付|承诺|提升|降低|达到|保证)"
)


def build_solution_v2_payload(
    context: SolutionContext,
    snapshot: RetrievalSnapshot,
    verified: VerifiedSolutionResult,
    *,
    verifier_version: str,
) -> dict[str, Any]:
    """Convert the internal verified workflow result into the frozen-method V2 payload."""
    packets = build_claim_packets(context, snapshot, verified.solution)
    final_reviews = {review.claim_id: review for review in verified.quality_report.reviews}
    claims: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    used_evidence: set[str] = set()
    verdict_labels = {
        "supported": "entailed",
        "unsupported": "contradicted",
        "not_documented": "insufficient",
    }
    for packet in packets:
        review = final_reviews[packet["claim_id"]]
        evidence_key = packet["evidence_key"]
        risk_level = _risk_level(packet["boundary"], packet["text"])
        label = verdict_labels[review.verdict.value]
        claims.append(
            {
                "claim_id": packet["claim_id"],
                "section": packet["section"],
                "text": packet["text"],
                "claim_type": packet["boundary"],
                "boundary": packet["boundary"],
                "risk_level": risk_level,
                "verification_status": label,
                "evidence_refs": [evidence_key] if evidence_key else [],
            }
        )
        if evidence_key:
            used_evidence.add(evidence_key)
            links.append(
                {
                    "claim_id": packet["claim_id"],
                    "evidence_key": evidence_key,
                    "label": label,
                    "score": None,
                    "verifier_version": verifier_version,
                }
            )

    evidence = [
        {
            "evidence_key": f"{item.asset_id}/{item.source_id}",
            "asset_id": item.asset_id,
            "source_id": item.source_id,
        }
        for item in [*snapshot.experiences, *snapshot.capabilities]
        if f"{item.asset_id}/{item.source_id}" in used_evidence
    ]
    attempts = [
        {
            "attempt": attempt.attempt,
            "candidate_version": attempt.attempt,
            "failed_claim_ids": attempt.quality_report.failed_claim_ids,
            "quality_report": attempt.quality_report.model_dump(mode="json"),
            "duration_ms": None,
        }
        for attempt in verified.attempts
    ]
    return {
        "schema_version": "solution-v2",
        "solution": verified.solution.model_dump(mode="json"),
        "claims": claims,
        "evidence": evidence,
        "claim_evidence_links": links,
        "quality_attempts": attempts,
        "verifier_version": verifier_version,
        "recommended_action": "release" if verified.passed else "review",
    }


def _risk_level(boundary: str, text: str) -> str:
    if boundary in {"historical_fact", "enterprise_capability"}:
        return "high"
    if boundary == "pending_confirmation":
        return "low"
    return "high" if _HIGH_RISK_PATTERN.search(text) else "medium"
