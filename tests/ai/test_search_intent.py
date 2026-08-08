import asyncio

import pytest
from pydantic import ValidationError

from app.ai.pipelines.search_intent import extract_search_intent, merge_unique
from app.ai.schemas import (
    ConversationMessage,
    CustomerProfileDraft,
    SolutionContext,
)


def make_context() -> SolutionContext:
    profile = CustomerProfileDraft(
        customer_name="星瀚精工集团",
        industry="离散制造",
        background="客户希望先做一条线的视觉质检试点。",
        current_problems=["人工图片复看压力大"],
        goals=["减少人工复看"],
        constraints=["不替换现有 MES", "图片不出园区", "不允许 AI 自动停线"],
        existing_systems=["MES 有查询接口"],
        information_gaps=["相机型号和图像质量", "试点验收指标"],
        profile_summary="离散制造客户希望做园区内、人工确认的单线视觉试点。",
        source_ids=["SRC-CUST-001"],
    )
    return SolutionContext(
        customer_profile=profile,
        current_requirement="别再让质量同事一张张看图了，但老 MES 千万别动。",
        conversation_history=[
            ConversationMessage(role="user", content="先在一条线上跑。"),
            ConversationMessage(role="assistant", content="还需要确认相机情况。"),
        ],
    )


class StubSearchIntentClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "任务不是回答客户，也不是生成方案" in system_prompt
        assert "先在一条线上跑" in user_prompt
        return {
            "current_requirement": "模型擅自改写的需求",
            "industry": "模型擅自改写的行业",
            "scenarios": ["单产线视觉质检"],
            "problems": ["人工图片复看压力大"],
            "goals": ["降低人工图片复看"],
            "hard_constraints": ["老 MES 不动", "先在单线运行"],
            "keywords": ["视觉质检", "旁路接入", "人工复核"],
            "missing_information": ["缺陷样本和标注状态"],
            "embedding_text": "模型乱写的检索文本",
        }


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.result


def test_extract_search_intent_preserves_context_and_builds_embedding_text() -> None:
    context = make_context()

    intent = asyncio.run(extract_search_intent(context, StubSearchIntentClient()))

    assert intent.current_requirement == context.current_requirement
    assert intent.industry == "离散制造"
    assert "不允许 AI 自动停线" in intent.hard_constraints
    assert "老 MES 不动" in intent.hard_constraints
    assert "相机型号和图像质量" in intent.missing_information
    assert "缺陷样本和标注状态" in intent.missing_information
    assert intent.embedding_text.startswith("当前需求：")
    assert "客户画像摘要" not in intent.embedding_text
    assert "模型乱写" not in intent.embedding_text
    assert context.current_requirement in intent.embedding_text
    assert intent.query_text == context.current_requirement
    assert intent.business_goal == "降低人工图片复看"
    assert "视觉质检" in intent.preferred_tags
    assert "不允许 AI 自动停线" in intent.excluded_claims
    assert intent.experience_query_text.startswith("历史项目要解决的问题：")
    assert intent.capability_query_text.startswith("当前要实现的能力：")
    assert intent.ranking_signals[0] == "hard_constraint_satisfaction"
    assert "相机型号和图像质量" in intent.expertise_gaps


def test_extract_search_intent_deduplicates_near_equivalent_constraints_and_gaps() -> None:
    context = make_context()
    context.customer_profile.constraints = ["不得替换或影响现有 MES 主流程"]
    context.customer_profile.information_gaps = ["现有相机型号、图像质量、覆盖范围和图片留存时间"]
    client = StaticJsonModelClient(
        {
            "scenarios": [],
            "problems": [],
            "goals": [],
            "hard_constraints": ["不替换现有 MES 主流程"],
            "keywords": [],
            "missing_information": ["现有相机型号/图像质量/覆盖范围/留存时间"],
        }
    )
    intent = asyncio.run(extract_search_intent(context, client))
    assert intent.hard_constraints == ["不得替换或影响现有 MES 主流程"]
    assert intent.missing_information == ["现有相机型号、图像质量、覆盖范围和图片留存时间"]


def test_merge_unique_deduplicates_semantically_equivalent_live_model_phrasing() -> None:
    merged_constraints = merge_unique(
        ["图片不得离开园区", "不允许 AI 自动停线或修改工艺参数"],
        ["图片数据不得离开园区", "禁止 AI 自动停线或修改工艺参数"],
    )
    merged_gaps = merge_unique(
        ["真实样本数量、缺陷分类与标注状态", "预算范围"],
        ["已标注缺陷样本数量、类别分布与标注质量", "预算范围或采购模式倾向"],
    )

    assert merged_constraints == ["图片不得离开园区", "不允许 AI 自动停线或修改工艺参数"]
    assert merged_gaps == ["真实样本数量、缺陷分类与标注状态", "预算范围"]


def test_extract_search_intent_rejects_extra_model_fields() -> None:
    client = StaticJsonModelClient(
        {
            "scenarios": [],
            "problems": [],
            "goals": [],
            "hard_constraints": [],
            "keywords": [],
            "missing_information": [],
            "unexpected": "不允许的字段",
        }
    )

    with pytest.raises(ValidationError):
        asyncio.run(extract_search_intent(make_context(), client))


@pytest.mark.parametrize("bad_value", [None, "不是数组", [123]])
def test_extract_search_intent_rejects_invalid_constraint_list(bad_value: object) -> None:
    client = StaticJsonModelClient(
        {
            "scenarios": [],
            "problems": [],
            "goals": [],
            "hard_constraints": bad_value,
            "keywords": [],
            "missing_information": [],
        }
    )

    with pytest.raises(ValueError, match="hard_constraints must be a list of strings"):
        asyncio.run(extract_search_intent(make_context(), client))
