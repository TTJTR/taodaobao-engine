import re
from difflib import SequenceMatcher
from typing import Protocol

from app.ai.pipelines.expert_routing import route_experts
from app.ai.prompts.search_intent import (
    SEARCH_INTENT_SYSTEM_PROMPT,
    build_search_intent_user_prompt,
)
from app.ai.schemas import SearchIntent, SolutionContext


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


SEMANTIC_DEDUP_RULES = (
    ("constraint:image-residency", (("图片", "图像"), ("离开园区", "出园区"))),
    ("constraint:ai-control", (("AI",), ("自动停线", "工艺参数"))),
    ("constraint:remote-write-security", (("远程升级",), ("接口写入",), ("安全评审",))),
    ("gap:camera", (("相机",), ("型号", "分辨率", "图像质量", "覆盖范围"))),
    ("gap:samples", (("样本",), ("缺陷", "标注"))),
    ("gap:acceptance", (("验收",), ("指标", "基线", "口径"))),
    ("gap:mes-interface", (("MES",), ("接口",))),
    ("gap:edge-server", (("边缘服务器",), ("规格", "位置", "部署窗口"))),
    ("gap:budget", (("预算",),)),
)


def semantic_dedup_key(value: str, fallback: str) -> str:
    for label, token_groups in SEMANTIC_DEDUP_RULES:
        matches_all_groups = all(
            any(token.casefold() in value.casefold() for token in group)
            for group in token_groups
        )
        if matches_all_groups:
            return label
    return fallback


def merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            normalized = value.strip()
            key = re.sub(r"[^\w]+", "", normalized.casefold())
            key = key.replace("不得", "不").replace("或影响", "")
            key = semantic_dedup_key(normalized, key)
            near_duplicate = any(
                key == existing
                or (
                    min(len(key), len(existing)) >= 18
                    and SequenceMatcher(None, key, existing).ratio() >= 0.86
                )
                for existing in seen
            )
            if normalized and key and not near_duplicate:
                merged.append(normalized)
                seen.add(key)
    return merged


def read_model_string_list(result: dict, field: str) -> list[str]:
    value = result.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"model field {field} must be a list of strings")
    return value


def build_search_embedding_text(context: SolutionContext, intent: SearchIntent) -> str:
    sections: list[tuple[str, str | None]] = [
        ("当前需求", intent.current_requirement),
        ("问题", "、".join(intent.problems) or None),
        ("目标", "、".join(intent.goals) or None),
        ("硬约束", "、".join(intent.hard_constraints) or None),
        ("行业", intent.industry),
        ("场景", "、".join(intent.scenarios) or None),
    ]
    return "".join(f"{title}：{content}。" for title, content in sections if content)


def derive_excluded_claims(hard_constraints: list[str]) -> list[str]:
    markers = ("不", "禁止", "不得", "不能", "只允许", "必须人工")
    return [constraint for constraint in hard_constraints if any(m in constraint for m in markers)]


def build_experience_query_text(intent: SearchIntent) -> str:
    sections = [
        ("历史项目要解决的问题", "、".join(intent.problems) or intent.current_requirement),
        ("行业", intent.industry),
        ("适用场景", "、".join(intent.scenarios) or None),
        ("硬约束", "、".join(intent.hard_constraints) or None),
    ]
    return "".join(f"{title}：{value}。" for title, value in sections if value)


def build_capability_query_text(intent: SearchIntent) -> str:
    sections = [
        ("当前要实现的能力", intent.current_requirement),
        ("业务目标", intent.business_goal),
        ("优先标签", "、".join(intent.preferred_tags) or None),
        ("不可违反", "、".join(intent.hard_constraints) or None),
    ]
    return "".join(f"{title}：{value}。" for title, value in sections if value)


async def extract_search_intent(
    context: SolutionContext,
    model_client: JsonModelClient,
) -> SearchIntent:
    if context.task_type == "expert_routing":
        return await route_experts(context, model_client)
    result = await model_client.generate_json(
        SEARCH_INTENT_SYSTEM_PROMPT,
        build_search_intent_user_prompt(context.model_dump_json(indent=2)),
    )

    profile = context.customer_profile
    result["current_requirement"] = context.current_requirement
    result["industry"] = profile.industry
    result["hard_constraints"] = merge_unique(
        profile.constraints,
        read_model_string_list(result, "hard_constraints"),
    )
    result["missing_information"] = merge_unique(
        profile.information_gaps,
        read_model_string_list(result, "missing_information"),
    )
    result["query_text"] = context.current_requirement
    result["business_goal"] = next(
        iter(read_model_string_list(result, "goals") or profile.goals),
        None,
    )
    result["preferred_tags"] = merge_unique(
        read_model_string_list(result, "preferred_tags"),
        read_model_string_list(result, "keywords"),
        read_model_string_list(result, "scenarios"),
    )
    result["excluded_claims"] = merge_unique(
        read_model_string_list(result, "excluded_claims"),
        derive_excluded_claims(result["hard_constraints"]),
    )
    result["ranking_signals"] = [
        "hard_constraint_satisfaction",
        "problem_relevance",
        "industry_scene_fit",
        "source_freshness",
        "vector_similarity",
    ]
    result["expertise_gaps"] = merge_unique(
        read_model_string_list(result, "expertise_gaps"),
        result["missing_information"],
    )
    result["experience_query_text"] = "由系统生成"
    result["capability_query_text"] = "由系统生成"
    result["embedding_text"] = "由系统生成"

    intent = SearchIntent.model_validate(result)
    return intent.model_copy(
        update={
            "embedding_text": build_search_embedding_text(context, intent),
            "experience_query_text": build_experience_query_text(intent),
            "capability_query_text": build_capability_query_text(intent),
        }
    )
