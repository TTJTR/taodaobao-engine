from difflib import SequenceMatcher
from typing import Protocol

from app.ai.prompts.capability import (
    CAPABILITY_SYSTEM_PROMPT,
    build_capability_user_prompt,
)
from app.ai.prompts.experience import (
    EXPERIENCE_SYSTEM_PROMPT,
    build_experience_user_prompt,
)
from app.ai.prompts.profile import PROFILE_SYSTEM_PROMPT, build_profile_user_prompt
from app.ai.schemas import CapabilityDraft, CustomerProfileDraft, ExperienceDraft, ProfileStatus

MAX_CAPABILITIES_PER_DOCUMENT = 20
MAX_RAW_TEXT_CHARACTERS = 80_000
AUDIT_REJECTION_TAGS = {"审核反例", "能力越界"}
PROFILE_FACT_FIELDS = {
    "industry",
    "background",
    "current_problems",
    "goals",
    "constraints",
    "existing_systems",
    "information_gaps",
}
PROFILE_LIST_FIELDS = {
    "current_problems",
    "goals",
    "constraints",
    "existing_systems",
    "information_gaps",
}
PROFILE_UNKNOWN_CUSTOMER_NAME = "待确认客户"
HISTORICAL_RECORD_MARKERS = (
    "完成试点验收",
    "完成验收",
    "通过验收",
    "验收记录",
    "实际运行",
    "上线运行",
    "复盘结果",
)
CONCEPT_ONLY_MARKERS = ("未实施", "尚未实施", "只是构想", "仅为构想", "概念设想")


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


def validate_raw_text(raw_text: str) -> str:
    normalized = raw_text.strip()
    if not normalized:
        raise ValueError("raw_text must not be empty")
    if len(normalized) > MAX_RAW_TEXT_CHARACTERS:
        raise ValueError(
            f"raw_text exceeds the {MAX_RAW_TEXT_CHARACTERS} character limit; split the source"
        )
    return normalized


def normalize_profile_result(result: dict) -> dict:
    normalized = dict(result)
    if "customer_name" in normalized:
        customer_name = normalized.get("customer_name")
        if isinstance(customer_name, list):
            customer_name = "、".join(
                item.strip() for item in customer_name if isinstance(item, str) and item.strip()
            )
        if not isinstance(customer_name, str) or not customer_name.strip():
            customer_name = PROFILE_UNKNOWN_CUSTOMER_NAME
        normalized["customer_name"] = customer_name.strip()

    for field in ("industry", "background"):
        value = normalized.get(field)
        if isinstance(value, list):
            value = "、".join(
                item.strip() for item in value if isinstance(item, str) and item.strip()
            )
        normalized[field] = value.strip() if isinstance(value, str) and value.strip() else None

    industry = normalized.get("industry")
    if isinstance(industry, list):
        normalized["industry"] = "、".join(
            item.strip() for item in industry if isinstance(item, str) and item.strip()
        ) or None
    for field in PROFILE_LIST_FIELDS:
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = [value]
        elif value is None:
            normalized[field] = []
    fact_sources = normalized.get("fact_sources")
    if isinstance(fact_sources, list):
        normalized["fact_sources"] = [
            item
            for item in fact_sources
            if isinstance(item, dict) and item.get("field") in PROFILE_FACT_FIELDS
        ]
    conflicts = normalized.get("conflicts")
    if isinstance(conflicts, list):
        valid_conflicts = []
        downgraded_questions = []
        for item in conflicts:
            if not isinstance(item, dict) or item.get("field") not in PROFILE_FACT_FIELDS:
                continue
            conflict = {
                key: item[key]
                for key in (
                    "field",
                    "conflicting_values",
                    "source_ids",
                    "clarification_question",
                )
                if key in item
            }
            conflicting_values = list(dict.fromkeys(conflict.get("conflicting_values") or []))
            conflict_source_ids = list(dict.fromkeys(conflict.get("source_ids") or []))
            if len(conflicting_values) < 2 or len(conflict_source_ids) < 2:
                question = conflict.get("clarification_question")
                if isinstance(question, str) and question.strip():
                    downgraded_questions.append(question.strip())
                continue
            conflict["conflicting_values"] = conflicting_values
            conflict["source_ids"] = conflict_source_ids
            valid_conflicts.append(conflict)
        normalized["conflicts"] = valid_conflicts
        normalized["information_gaps"] = list(
            dict.fromkeys([*normalized.get("information_gaps", []), *downgraded_questions])
        )
    return normalized


def normalize_experience_evidence_status(
    raw_text: str,
    result: dict,
) -> None:
    if any(marker in raw_text for marker in CONCEPT_ONLY_MARKERS):
        result["evidence_status"] = "concept_only"
        return
    if result.get("result") and any(marker in raw_text for marker in HISTORICAL_RECORD_MARKERS):
        result["evidence_status"] = "historical_record"


async def extract_profile_from_text(
    raw_text: str,
    source_ids: list[str],
    model_client: JsonModelClient,
) -> CustomerProfileDraft:
    raw_text = validate_raw_text(raw_text)
    normalized_source_ids = [source_id.strip() for source_id in source_ids if source_id.strip()]
    if not normalized_source_ids:
        raise ValueError("source_ids must not be empty")

    result = await model_client.generate_json(
        PROFILE_SYSTEM_PROMPT,
        build_profile_user_prompt(raw_text, normalized_source_ids),
    )
    result = normalize_profile_result(result)
    result["source_ids"] = normalized_source_ids
    result["profile_status"] = ProfileStatus.PENDING_CONFIRMATION
    result.setdefault("fact_sources", [])
    result.setdefault("conflicts", [])
    profile = CustomerProfileDraft.model_validate(result)
    allowed_sources = set(normalized_source_ids)
    if any(fact.source_id not in allowed_sources for fact in profile.fact_sources):
        raise ValueError("profile fact_sources contains an unknown source_id")
    has_unknown_conflict_source = any(
        not set(conflict.source_ids).issubset(allowed_sources) for conflict in profile.conflicts
    )
    if has_unknown_conflict_source:
        raise ValueError("profile conflicts contains an unknown source_id")
    conflict_questions = [conflict.clarification_question for conflict in profile.conflicts]
    merged_gaps = list(dict.fromkeys([*profile.information_gaps, *conflict_questions]))
    return profile.model_copy(update={"information_gaps": merged_gaps})


def build_experience_embedding_text(experience: ExperienceDraft) -> str:
    sections = [
        ("经验名称", experience.name),
        ("适用问题", experience.problem),
        ("主要方案", experience.solution),
        ("前置条件", experience.prerequisites),
        ("适用场景", experience.applicable_conditions),
        ("风险", experience.risks),
        ("标签", "、".join(experience.tags) or None),
    ]
    return "".join(f"{title}：{content}。" for title, content in sections if content)


async def extract_experience_from_text(
    raw_text: str,
    source_id: str,
    model_client: JsonModelClient,
) -> ExperienceDraft:
    raw_text = validate_raw_text(raw_text)
    if not source_id.strip():
        raise ValueError("source_id must not be empty")

    result = await model_client.generate_json(
        EXPERIENCE_SYSTEM_PROMPT,
        build_experience_user_prompt(raw_text, source_id),
    )
    result["source_id"] = source_id
    result["embedding_text"] = None
    normalize_experience_evidence_status(raw_text, result)
    experience = ExperienceDraft.model_validate(result)
    return experience.model_copy(
        update={"embedding_text": build_experience_embedding_text(experience)}
    )


def build_capability_embedding_text(capability: CapabilityDraft) -> str:
    sections = [
        ("能力名称", capability.name),
        ("能力说明", capability.description),
        ("输入", capability.input),
        ("输出", capability.output),
        ("前置条件", capability.prerequisites),
        ("限制", capability.limitations),
        ("标签", "、".join(capability.tags) or None),
    ]
    return "".join(f"{title}：{content}。" for title, content in sections if content)


def normalize_capability_candidate(raw_capability: dict, source_id: str) -> dict:
    normalized = {**raw_capability, "source_id": source_id, "embedding_text": None}
    if "inputs" not in normalized and "input" in normalized:
        normalized["inputs"] = normalized.pop("input")
    if "outputs" not in normalized and "output" in normalized:
        normalized["outputs"] = normalized.pop("output")
    for field in ("limitations", "prerequisites"):
        value = normalized.get(field)
        if isinstance(value, list):
            normalized[field] = "；".join(
                item.strip() for item in value if isinstance(item, str) and item.strip()
            ) or None
    for field in ("dependencies", "tags", "review_warnings"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = [value]

    warnings = list(normalized.get("review_warnings") or [])
    if normalized.get("inputs") is None:
        normalized["inputs"] = []
    if normalized.get("outputs") is None:
        normalized["outputs"] = []
    for field, label in (("inputs", "输入"), ("outputs", "输出"), ("limitations", "限制")):
        if not normalized.get(field):
            warnings.append(f"原文未明确{label}，需人工补充后才能通过审核")
    normalized["review_warnings"] = list(dict.fromkeys(warnings))
    return normalized


def add_semantic_merge_suggestions(
    capabilities: list[CapabilityDraft],
) -> list[CapabilityDraft]:
    suggested: list[CapabilityDraft] = []
    for capability in capabilities:
        best_match: CapabilityDraft | None = None
        best_score = 0.0
        for previous in suggested:
            score = SequenceMatcher(
                None,
                capability.name.casefold(),
                previous.name.casefold(),
            ).ratio()
            if score > best_score:
                best_score = score
                best_match = previous
        if best_match is not None and best_score >= 0.82:
            capability = capability.model_copy(
                update={"merge_suggestion": f"可能与“{best_match.name}”语义重复，请人工合并"}
            )
        suggested.append(capability)
    return suggested


def source_explicitly_requests_audit_rejection(raw_text: str) -> bool:
    """Only keep rejected candidates when the PRD explicitly asks reviewers to reject one."""
    return "打回" in raw_text and any(
        marker in raw_text for marker in ("候选", "抽成企业能力", "能力审核")
    )


def is_audit_rejection(capability: CapabilityDraft) -> bool:
    return bool(AUDIT_REJECTION_TAGS.intersection(capability.tags))


async def extract_capabilities_from_text(
    raw_text: str,
    source_id: str,
    model_client: JsonModelClient,
) -> list[CapabilityDraft]:
    raw_text = validate_raw_text(raw_text)
    normalized_source_id = source_id.strip()
    if not normalized_source_id:
        raise ValueError("source_id must not be empty")

    result = await model_client.generate_json(
        CAPABILITY_SYSTEM_PROMPT,
        build_capability_user_prompt(raw_text, normalized_source_id),
    )
    if set(result) != {"capabilities"}:
        raise ValueError("model output must contain only capabilities")
    raw_capabilities = result["capabilities"]
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ValueError("model output must contain at least one capability")
    if len(raw_capabilities) > MAX_CAPABILITIES_PER_DOCUMENT:
        raise ValueError(
            f"model output contains more than {MAX_CAPABILITIES_PER_DOCUMENT} capabilities"
        )

    capabilities = []
    allows_audit_rejections = source_explicitly_requests_audit_rejection(raw_text)
    for raw_capability in raw_capabilities:
        if not isinstance(raw_capability, dict):
            raise ValueError("each capability must be a JSON object")
        normalized_capability = normalize_capability_candidate(
            raw_capability,
            normalized_source_id,
        )
        capability = CapabilityDraft.model_validate(normalized_capability)
        if is_audit_rejection(capability) and not allows_audit_rejections:
            continue
        capabilities.append(
            capability.model_copy(
                update={"embedding_text": build_capability_embedding_text(capability)}
            )
        )

    if not capabilities:
        raise ValueError("model output contains no valid capability after boundary checks")
    return add_semantic_merge_suggestions(capabilities)
