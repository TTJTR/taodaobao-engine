import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.search_intent import extract_search_intent
from app.ai.schemas import (
    CandidateRecord,
    ContributionEvidence,
    CustomerProfileDraft,
    ResearchContextPackage,
    RetrievalSnapshot,
    SolutionContext,
)


def make_context(candidates: list[CandidateRecord]) -> SolutionContext:
    snapshot = RetrievalSnapshot(
        experiences=[],
        capabilities=[],
        created_at=datetime.now(UTC),
    )
    package = ResearchContextPackage(
        research_task_id="RESEARCH-001",
        conversation_summary="客户要确认旧系统写入权限。",
        research_document_summary="旁路方案可行，但写入权限没有证据。",
        profile_summary="制造业客户，不允许影响主系统。",
        evidence_snapshot=snapshot,
        knowledge_gaps=["MES 写入权限和安全审批条件"],
        questions=[],
    )
    return SolutionContext(
        customer_profile=CustomerProfileDraft(
            customer_name="测试客户",
            industry="制造业",
            constraints=["不得影响主系统"],
            profile_summary="制造业客户，不允许影响主系统。",
            source_ids=["SRC-CUST"],
        ),
        current_requirement="为研究缺口匹配真实专家。",
        research_task_id="RESEARCH-001",
        task_type="expert_routing",
        research_context=package,
        candidate_records=candidates,
    )


def make_candidate() -> CandidateRecord:
    return CandidateRecord(
        candidate_id="USER-001",
        display_name="李同学",
        contributions=[
            ContributionEvidence(
                contribution_id="CONTRIB-001",
                source_id="SRC-PRD-SECURITY",
                title="旧系统接口安全评审说明",
                tags=["MES", "安全评审"],
                reviewed=True,
            )
        ],
    )


class RoutingClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "不能生成姓名" in system_prompt
        assert "CONTRIB-001" in user_prompt
        return {
            "expertise_gaps": ["MES 写入权限和安全审批条件"],
            "ranked_candidates": [
                {
                    "candidate_id": "USER-001",
                    "reason": "贡献过旧系统接口安全评审说明",
                    "contribution_ids": ["CONTRIB-001"],
                }
            ],
            "questions": [
                {
                    "question_id": "Q1",
                    "question": "在不影响 MES 主流程时，开放写入接口需要哪些安全审批？",
                    "required_roles": ["接口安全"],
                    "sensitive": False,
                }
            ],
            "do_not_invite_reason": None,
        }


def test_expert_routing_only_ranks_real_candidates_with_real_contributions() -> None:
    intent = asyncio.run(extract_search_intent(make_context([make_candidate()]), RoutingClient()))

    assert intent.ranked_candidate_ids == ["USER-001"]
    assert intent.candidate_reasons[0].contribution_ids == ["CONTRIB-001"]
    assert "安全审批" in intent.expert_questions[0].question


class InventedCandidateClient(RoutingClient):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        result = await super().generate_json(system_prompt, user_prompt)
        result["ranked_candidates"][0]["candidate_id"] = "USER-INVENTED"
        return result


def test_expert_routing_rejects_invented_candidate() -> None:
    with pytest.raises(ValueError, match="invented a candidate_id"):
        asyncio.run(
            extract_search_intent(
                make_context([make_candidate()]),
                InventedCandidateClient(),
            )
        )


def test_expert_routing_returns_empty_when_backend_provides_no_candidates() -> None:
    intent = asyncio.run(extract_search_intent(make_context([]), MustNotCallRoutingClient()))

    assert intent.ranked_candidate_ids == []
    assert "未提供真实候选人" in intent.do_not_invite_reason


class MustNotCallRoutingClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise AssertionError("no-candidate routing must not call the model")
