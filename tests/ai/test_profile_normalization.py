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
