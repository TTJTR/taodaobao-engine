import json
from typing import Protocol

from app.ai.prompts.quality import QUALITY_SYSTEM_PROMPT, build_quality_user_prompt
from app.ai.schemas import (
    ClaimReview,
    ClaimVerdict,
    EvidenceBoundary,
    QualityReport,
    RetrievalSnapshot,
    Solution,
    SolutionContext,
)

SOLUTION_CONTENT_FIELDS = (
    "requirement_understanding",
    "initial_recommendations",
    "historical_evidence",
    "capability_composition",
    "prerequisites_and_risks",
    "pending_confirmations",
)


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


def build_asset_evidence(snapshot: RetrievalSnapshot) -> dict[tuple[str, str], dict]:
    evidence = {
        (item.asset_id, item.source_id): {
            "asset_type": "historical_experience",
            "asset_id": item.asset_id,
            "source_id": item.source_id,
            "content": item.data.model_dump(mode="json"),
        }
        for item in snapshot.experiences
    }
    evidence.update(
        {
            (item.asset_id, item.source_id): {
                "asset_type": "enterprise_capability",
                "asset_id": item.asset_id,
                "source_id": item.source_id,
                "content": item.data.model_dump(mode="json"),
            }
            for item in snapshot.capabilities
        }
    )
    return evidence


def build_claim_packets(
    context: SolutionContext,
    snapshot: RetrievalSnapshot,
    solution: Solution,
) -> list[dict]:
    asset_evidence = build_asset_evidence(snapshot)
    packets: list[dict] = []
    for field in SOLUTION_CONTENT_FIELDS:
        for index, item in enumerate(getattr(solution, field), start=1):
            claim_id = f"{field}:{index}"
            if item.boundary in {
                EvidenceBoundary.HISTORICAL_FACT,
                EvidenceBoundary.ENTERPRISE_CAPABILITY,
            }:
                evidence = asset_evidence.get((item.asset_id, item.source_id))
                if evidence is None:
                    raise ValueError(
                        f"solution citation {item.asset_id}/{item.source_id} "
                        "is not in the retrieval snapshot"
                    )
                evidence_key = f"{item.asset_id}/{item.source_id}"
            else:
                evidence_key = None
            packets.append(
                {
                    "claim_id": claim_id,
                    "section": field,
                    "text": item.text,
                    "boundary": item.boundary.value,
                    "asset_id": item.asset_id,
                    "source_id": item.source_id,
                    "evidence_key": evidence_key,
                }
            )
    if not packets:
        raise ValueError("solution must contain at least one reviewable claim")
    return packets


def build_review_bundle(
    context: SolutionContext,
    snapshot: RetrievalSnapshot,
    solution: Solution,
) -> dict:
    asset_evidence = build_asset_evidence(snapshot)
    return {
        "shared_context": {
            "customer_profile": context.customer_profile.model_dump(mode="json"),
            "current_requirement": context.current_requirement,
            "retrieval_gap_summary": snapshot.gap_summary,
            "retrieval_missing_information": snapshot.missing_information,
            "evidence_conflicts": [
                conflict.model_dump(mode="json") for conflict in snapshot.conflicts
            ],
            "can_generate_solution": snapshot.can_generate_solution,
        },
        "asset_evidence": {
            f"{asset_id}/{source_id}": evidence
            for (asset_id, source_id), evidence in asset_evidence.items()
        },
        "claims": build_claim_packets(context, snapshot, solution),
    }


def validate_review_coverage(result: dict, expected_claim_ids: list[str]) -> list[ClaimReview]:
    if set(result) != {"reviews"}:
        raise ValueError("quality model output must contain only reviews")
    raw_reviews = result["reviews"]
    if not isinstance(raw_reviews, list):
        raise ValueError("quality model reviews must be a list")
    reviews = [ClaimReview.model_validate(item) for item in raw_reviews]
    actual_claim_ids = [review.claim_id for review in reviews]
    if actual_claim_ids != expected_claim_ids:
        raise ValueError("quality reviews must cover every claim once and in order")
    return reviews


async def quality_check_solution(
    context: SolutionContext,
    retrieval_snapshot: RetrievalSnapshot,
    solution: Solution,
    model_client: JsonModelClient,
) -> QualityReport:
    review_bundle = build_review_bundle(context, retrieval_snapshot, solution)
    packets = review_bundle["claims"]
    result = await model_client.generate_json(
        QUALITY_SYSTEM_PROMPT,
        build_quality_user_prompt(json.dumps(review_bundle, ensure_ascii=False, indent=2)),
    )
    reviews = validate_review_coverage(
        result,
        [packet["claim_id"] for packet in packets],
    )
    failed_claim_ids = [
        review.claim_id for review in reviews if review.verdict != ClaimVerdict.SUPPORTED
    ]
    passed = not failed_claim_ids
    summary = (
        "全部报告论断均有相应证据或正确标明边界，可以进入人工审核。"
        if passed
        else f"有 {len(failed_claim_ids)} 条论断未通过独立质检，需要修改后重新检查。"
    )
    return QualityReport(
        reviews=reviews,
        passed=passed,
        requires_regeneration=not passed,
        failed_claim_ids=failed_claim_ids,
        summary=summary,
    )
