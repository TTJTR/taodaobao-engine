import asyncio

from app.ai.pipelines.extractor import extract_profile_from_text
from app.services.intelligence_service import _profile_diff


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        del system_prompt, user_prompt
        return self.result


def test_extract_profile_normalizes_missing_customer_and_empty_scalar_lists() -> None:
    result = {
        "customer_name": None,
        "industry": [],
        "background": [],
        "current_problems": [],
        "goals": [],
        "constraints": [],
        "existing_systems": [],
        "information_gaps": ["原文没有明确客户画像事实"],
        "profile_summary": "公开页面没有出现明确客户信息。",
        "fact_sources": [],
        "conflicts": [],
    }

    profile = asyncio.run(
        extract_profile_from_text(
            "一份没有明确客户主体的公开资料",
            ["SRC-PUBLIC-001"],
            StaticJsonModelClient(result),
        )
    )

    assert profile.customer_name == "待确认客户"
    assert profile.industry is None
    assert profile.background is None
    assert profile.profile_status == "pending_confirmation"
    assert profile.source_ids == ["SRC-PUBLIC-001"]


def test_profile_diff_excludes_ai_schema_metadata() -> None:
    patch = _profile_diff(
        {"external_intelligence": {}},
        {
            "customer_name": "待确认客户",
            "profile_status": "pending_confirmation",
            "profile_summary": "没有明确客户事实。",
            "source_ids": ["SRC-PUBLIC-001"],
            "fact_sources": [],
            "conflicts": [],
            "information_gaps": ["需要确认客户主体"],
        },
    )

    assert set(patch) == {"information_gaps"}


def test_extract_profile_ignores_explanatory_conflict_fields_from_live_model() -> None:
    result = {
        "customer_name": "东岳智行",
        "industry": "汽车制造",
        "background": "全国多基地汽车制造企业。",
        "current_problems": ["知识分散"],
        "goals": ["统一知识入口"],
        "constraints": [],
        "existing_systems": ["飞书"],
        "information_gaps": [],
        "profile_summary": "东岳智行需要建设可信知识平台。",
        "fact_sources": [],
        "conflicts": [
            {
                "field": "goals",
                "conflicting_values": ["三个月", "六个月"],
                "source_ids": ["SRC-1", "SRC-2"],
                "clarification_question": "试点周期是三个月还是六个月？",
                "description": "两份材料的周期口径不同。",
                "quote": "材料一写三个月，材料二写六个月。",
            }
        ],
    }

    profile = asyncio.run(
        extract_profile_from_text(
            "汽车制造客户画像材料",
            ["SRC-1", "SRC-2"],
            StaticJsonModelClient(result),
        )
    )

    assert profile.conflicts[0].field == "goals"
    assert "试点周期是三个月还是六个月？" in profile.information_gaps


def test_extract_profile_downgrades_single_source_conflict_to_information_gap() -> None:
    result = {
        "customer_name": "东岳智行",
        "industry": "汽车制造",
        "background": "全国多基地汽车制造企业。",
        "current_problems": [],
        "goals": [],
        "constraints": [],
        "existing_systems": [],
        "information_gaps": [],
        "profile_summary": "客户画像待进一步确认。",
        "fact_sources": [],
        "conflicts": [
            {
                "field": "goals",
                "conflicting_values": ["待确定"],
                "source_ids": ["SRC-1"],
                "clarification_question": "请确认最终上线时间。",
                "description": "单份资料口径尚不明确。",
            }
        ],
    }

    profile = asyncio.run(
        extract_profile_from_text(
            "汽车制造客户画像材料",
            ["SRC-1"],
            StaticJsonModelClient(result),
        )
    )

    assert profile.conflicts == []
    assert "请确认最终上线时间。" in profile.information_gaps
