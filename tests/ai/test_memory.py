import asyncio

import pytest

from app.ai.pipelines.memory import suggest_profile_memory_updates
from app.ai.schemas import CustomerProfileDraft


def make_profile() -> CustomerProfileDraft:
    return CustomerProfileDraft(
        customer_name="测试客户",
        industry="制造业",
        constraints=["数据不出园区"],
        profile_summary="制造业客户，数据不出园区。",
        source_ids=["SRC-OLD"],
    )


class MemoryClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "绝不能把新资料直接写回画像" in system_prompt
        assert "SRC-NEW" in user_prompt
        return {
            "suggestions": [
                {
                    "operation": "add",
                    "field": "goals",
                    "proposed_value": "三个月内完成单线试点",
                    "previous_value": None,
                    "reason": "新会议中客户明确提出",
                    "source_ids": ["SRC-NEW"],
                    "source_quote": "希望三个月内先跑通一条线",
                    "status": "confirmed",
                }
            ]
        }


def test_memory_only_returns_pending_suggestions_without_mutating_profile() -> None:
    profile = make_profile()

    proposal = asyncio.run(
        suggest_profile_memory_updates(
            profile,
            "客户说：希望三个月内先跑通一条线。",
            ["SRC-NEW"],
            MemoryClient(),
        )
    )

    assert profile.goals == []
    assert proposal.current_profile.goals == []
    assert proposal.confirmation_required is True
    assert proposal.suggestions[0].status == "pending_confirmation"
    assert proposal.suggestions[0].proposed_value == "三个月内完成单线试点"


class InventedSourceClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "suggestions": [
                {
                    "operation": "add",
                    "field": "goals",
                    "proposed_value": "虚构目标",
                    "previous_value": None,
                    "reason": "模型猜测",
                    "source_ids": ["SRC-INVENTED"],
                    "source_quote": "不存在的原话",
                    "status": "pending_confirmation",
                }
            ]
        }


def test_memory_rejects_invented_source_ids() -> None:
    with pytest.raises(ValueError, match="unknown source_id"):
        asyncio.run(
            suggest_profile_memory_updates(
                make_profile(),
                "一段新资料",
                ["SRC-NEW"],
                InventedSourceClient(),
            )
        )
