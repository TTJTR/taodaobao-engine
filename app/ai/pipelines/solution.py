from collections.abc import Iterable
from copy import deepcopy
from typing import Protocol

from app.ai.prompts.solution import SOLUTION_SYSTEM_PROMPT, build_solution_user_prompt
from app.ai.schemas import (
    EvidenceBoundary,
    RetrievalSnapshot,
    Solution,
    SolutionContext,
    SourceReference,
)

SOLUTION_CONTENT_FIELDS = (
    "requirement_understanding",
    "initial_recommendations",
    "historical_evidence",
    "capability_composition",
    "prerequisites_and_risks",
    "pending_confirmations",
)
MODEL_SOLUTION_FIELDS = {*SOLUTION_CONTENT_FIELDS, "suggested_questions"}
SECTION_BOUNDARIES = {
    "requirement_understanding": {
        EvidenceBoundary.AI_INFERENCE,
        EvidenceBoundary.PENDING_CONFIRMATION,
    },
    "initial_recommendations": {EvidenceBoundary.AI_INFERENCE},
    "historical_evidence": {EvidenceBoundary.HISTORICAL_FACT},
    "capability_composition": {EvidenceBoundary.ENTERPRISE_CAPABILITY},
    "prerequisites_and_risks": set(EvidenceBoundary),
    "pending_confirmations": {EvidenceBoundary.PENDING_CONFIRMATION},
}
FIXED_SECTION_BOUNDARIES = {
    "initial_recommendations": EvidenceBoundary.AI_INFERENCE,
    "historical_evidence": EvidenceBoundary.HISTORICAL_FACT,
    "capability_composition": EvidenceBoundary.ENTERPRISE_CAPABILITY,
    "pending_confirmations": EvidenceBoundary.PENDING_CONFIRMATION,
}
PENDING_TEXT_MARKERS = ("待确认", "尚未确认", "未确认", "信息不足", "需确认")
ALWAYS_UNCITED_SECTIONS = {
    "requirement_understanding",
    "initial_recommendations",
    "pending_confirmations",
}


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


def build_allowed_citations(
    snapshot: RetrievalSnapshot,
) -> dict[tuple[str, str], EvidenceBoundary]:
    citations = {
        (item.asset_id, item.source_id): EvidenceBoundary.HISTORICAL_FACT
        for item in snapshot.experiences
    }
    citations.update(
        {
            (item.asset_id, item.source_id): EvidenceBoundary.ENTERPRISE_CAPABILITY
            for item in snapshot.capabilities
        }
    )
    return citations


def infer_uncited_boundary(field: str, text: object) -> EvidenceBoundary:
    if field == "pending_confirmations":
        return EvidenceBoundary.PENDING_CONFIRMATION
    if field in {"requirement_understanding", "prerequisites_and_risks"} and isinstance(text, str):
        if any(marker in text for marker in PENDING_TEXT_MARKERS):
            return EvidenceBoundary.PENDING_CONFIRMATION
    return FIXED_SECTION_BOUNDARIES.get(field, EvidenceBoundary.AI_INFERENCE)


def normalize_model_boundaries(result: dict, snapshot: RetrievalSnapshot) -> None:
    """Repair harmless label drift while keeping citation ownership strict."""
    allowed_citations = build_allowed_citations(snapshot)
    for field in SOLUTION_CONTENT_FIELDS:
        items = result.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if field in ALWAYS_UNCITED_SECTIONS:
                item["asset_id"] = None
                item["source_id"] = None
                item["boundary"] = infer_uncited_boundary(field, item.get("text")).value
                continue
            asset_id = item.get("asset_id")
            source_id = item.get("source_id")
            if bool(asset_id) != bool(source_id):
                matching_pairs = [
                    pair
                    for pair in allowed_citations
                    if (asset_id and pair[0] == asset_id) or (source_id and pair[1] == source_id)
                ]
                if len(matching_pairs) == 1:
                    asset_id, source_id = matching_pairs[0]
                    item["asset_id"] = asset_id
                    item["source_id"] = source_id
                elif field == "prerequisites_and_risks":
                    item["asset_id"] = None
                    item["source_id"] = None
                    item["boundary"] = infer_uncited_boundary(field, item.get("text")).value
                    continue
                else:
                    raise ValueError(
                        "asset_id and source_id must either both exist or identify one known asset"
                    )
            if asset_id and source_id:
                expected_boundary = allowed_citations.get((asset_id, source_id))
                if expected_boundary is not None and expected_boundary in SECTION_BOUNDARIES[field]:
                    item["boundary"] = expected_boundary.value
            else:
                item["boundary"] = infer_uncited_boundary(field, item.get("text")).value


def validate_model_citations(result: dict, snapshot: RetrievalSnapshot) -> None:
    allowed_citations = build_allowed_citations(snapshot)
    known_asset_ids = {asset_id for asset_id, _ in allowed_citations}
    for field in SOLUTION_CONTENT_FIELDS:
        items = result[field]
        if not isinstance(items, list):
            raise ValueError(f"model field {field} must be a list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"every item in {field} must be an object")
            try:
                boundary = EvidenceBoundary(item.get("boundary"))
            except ValueError as exc:
                raise ValueError(f"invalid evidence boundary in {field}") from exc
            if boundary not in SECTION_BOUNDARIES[field]:
                raise ValueError(f"boundary {boundary.value} is not allowed in {field}")

            asset_id = item.get("asset_id")
            source_id = item.get("source_id")
            text = item.get("text")
            mentioned_asset_ids = (
                {candidate for candidate in known_asset_ids if candidate in text}
                if isinstance(text, str)
                else set()
            )
            if asset_id and mentioned_asset_ids.difference({asset_id}):
                raise ValueError("one cited item must not mention another asset id")
            if not asset_id and mentioned_asset_ids:
                raise ValueError("uncited items must not mention enterprise asset ids")
            if boundary in {
                EvidenceBoundary.HISTORICAL_FACT,
                EvidenceBoundary.ENTERPRISE_CAPABILITY,
            }:
                expected_boundary = allowed_citations.get((asset_id, source_id))
                if expected_boundary is None:
                    raise ValueError(
                        f"citation {asset_id}/{source_id} is not in the retrieval snapshot"
                    )
                if boundary != expected_boundary:
                    raise ValueError(
                        f"citation {asset_id}/{source_id} uses the wrong evidence boundary"
                    )
            elif asset_id is not None or source_id is not None:
                raise ValueError(f"{boundary.value} items must not contain source identifiers")


def build_source_references(snapshot: RetrievalSnapshot) -> list[SourceReference]:
    sources = [
        SourceReference(
            asset_id=item.asset_id,
            source_id=item.source_id,
            title=item.data.name,
        )
        for item in snapshot.experiences
    ]
    sources.extend(
        SourceReference(
            asset_id=item.asset_id,
            source_id=item.source_id,
            title=item.data.name,
        )
        for item in snapshot.capabilities
    )
    return sources


def filter_source_references_to_used(
    sources: list[SourceReference],
    result: dict,
) -> list[SourceReference]:
    used_pairs = {
        (item.get("asset_id"), item.get("source_id"))
        for field in SOLUTION_CONTENT_FIELDS
        for item in result[field]
        if isinstance(item, dict) and item.get("asset_id") and item.get("source_id")
    }
    return [source for source in sources if (source.asset_id, source.source_id) in used_pairs]


def append_missing_confirmations(
    result: dict,
    missing_information: Iterable[str],
) -> None:
    pending_items = result["pending_confirmations"]
    existing_texts = {
        item.get("text", "").strip().casefold() for item in pending_items if isinstance(item, dict)
    }
    for information in missing_information:
        normalized = information.strip()
        if normalized and normalized.casefold() not in existing_texts:
            pending_items.append(
                {
                    "text": normalized,
                    "boundary": EvidenceBoundary.PENDING_CONFIRMATION,
                    "asset_id": None,
                    "source_id": None,
                }
            )
            existing_texts.add(normalized.casefold())


def prepend_no_basis_notice(result: dict) -> None:
    notice = "当前知识库无足够依据，不能按当前要求生成正式方案。"
    existing_texts = {
        item.get("text", "").strip()
        for item in result["requirement_understanding"]
        if isinstance(item, dict)
    }
    if notice not in existing_texts:
        result["requirement_understanding"].insert(
            0,
            {
                "text": notice,
                "boundary": EvidenceBoundary.AI_INFERENCE,
                "asset_id": None,
                "source_id": None,
            },
        )


def insert_opening_line_once(result: dict, opening_line: str | None) -> None:
    if not opening_line:
        return
    normalized = opening_line.strip()
    for field in SOLUTION_CONTENT_FIELDS:
        items = result[field]
        result[field] = [
            item
            for item in items
            if not isinstance(item, dict) or item.get("text", "").strip() != normalized
        ]
    result["requirement_understanding"].insert(
        0,
        {
            "text": normalized,
            "boundary": EvidenceBoundary.AI_INFERENCE,
            "asset_id": None,
            "source_id": None,
        },
    )


async def generate_solution(
    context: SolutionContext,
    retrieval_snapshot: RetrievalSnapshot,
    model_client: JsonModelClient,
) -> Solution:
    result = await model_client.generate_json(
        SOLUTION_SYSTEM_PROMPT,
        build_solution_user_prompt(
            context.model_dump_json(indent=2),
            retrieval_snapshot.model_dump_json(indent=2),
        ),
    )
    return finalize_solution_result(context, retrieval_snapshot, result)


def finalize_solution_result(
    context: SolutionContext,
    retrieval_snapshot: RetrievalSnapshot,
    result: dict,
) -> Solution:
    result = deepcopy(result)
    if set(result) != MODEL_SOLUTION_FIELDS:
        raise ValueError("model output does not match the required solution fields")

    normalize_model_boundaries(result, retrieval_snapshot)
    insert_opening_line_once(result, context.opening_line)
    if not retrieval_snapshot.can_generate_solution:
        prepend_no_basis_notice(result)
    append_missing_confirmations(
        result,
        [
            *context.customer_profile.information_gaps,
            *retrieval_snapshot.missing_information,
            *(conflict.clarification_question for conflict in retrieval_snapshot.conflicts),
        ],
    )
    validate_model_citations(result, retrieval_snapshot)
    result["sources"] = [
        source.model_dump(mode="json")
        for source in filter_source_references_to_used(
            build_source_references(retrieval_snapshot),
            result,
        )
    ]
    return Solution.model_validate(result)
