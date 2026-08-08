import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.deep_research import generate_deep_research_stage
from app.ai.schemas import (
    CustomerProfileDraft,
    ExpertAnswerInput,
    ResearchFinding,
    RetrievalSnapshot,
    SolutionContext,
)


def make_context(stage: str, **updates: object) -> SolutionContext:
    values = {
        "customer_profile": CustomerProfileDraft(
            customer_name="测试客户",
            industry="制造业",
            profile_summary="客户希望做可追溯的试点。",
            source_ids=["SRC-CUST"],
        ),
        "current_requirement": "比较可行路线并说明证据缺口。",
        "mode": "deep",
        "stage": stage,
        "trace_id": "TRACE-001",
        "research_task_id": "RESEARCH-001",
    }
    values.update(updates)
    return SolutionContext.model_validate(values)


def make_snapshot() -> RetrievalSnapshot:
    return RetrievalSnapshot(
        experiences=[],
        capabilities=[],
        missing_information=["验收指标"],
        can_generate_solution=False,
        created_at=datetime.now(UTC),
    )


class PlanningClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "一次只完成输入指定的 stage" in system_prompt
        assert "当前阶段：planning" in user_prompt
        return {
            "research_plan": {
                "objective": "比较试点路线并识别缺口",
                "subquestions": [
                    {
                        "question_id": "Q1",
                        "question": "现有证据支持哪些路线？",
                        "completion_condition": "每条路线都有证据或明确无依据",
                        "status": "pending",
                    }
                ],
                "completion_conditions": ["完成路线、风险和证据核验"],
            },
            "findings": [],
            "routes": [],
            "audit": None,
            "expert_questions": [],
        }


def test_deep_research_planning_returns_bounded_plan_and_shared_trace() -> None:
    result = asyncio.run(
        generate_deep_research_stage(make_context("planning"), make_snapshot(), PlanningClient())
    )

    assert result.trace_id == "TRACE-001"
    assert result.research_task_id == "RESEARCH-001"
    assert result.stage == "planning"
    assert result.research_plan is not None
    assert len(result.research_plan.subquestions) == 1
    assert result.status == "running"


class MissingQuestionIdsClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "research_plan": {
                "objective": "补齐试点信息",
                "subquestions": [
                    {
                        "question": "相机是否可用？",
                        "completion_condition": "获得相机清单",
                        "status": "pending",
                    },
                    {
                        "question": "验收口径是什么？",
                        "completion_condition": "获得验收指标",
                        "status": "pending",
                    },
                ],
                "completion_conditions": ["关键信息齐全"],
            },
            "findings": [],
            "routes": [],
            "audit": None,
            "expert_questions": [],
        }


def test_deep_research_assigns_stable_ids_when_model_omits_question_ids() -> None:
    result = asyncio.run(
        generate_deep_research_stage(
            make_context("planning"), make_snapshot(), MissingQuestionIdsClient()
        )
    )
    assert [item.question_id for item in result.research_plan.subquestions] == ["Q1", "Q2"]


class InventedRouteClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "research_plan": None,
            "findings": [],
            "routes": [
                {
                    "route_id": "R1",
                    "name": "虚构路线",
                    "summary": "引用了不存在的能力",
                    "tradeoffs": [],
                    "unsuitable_conditions": [],
                    "supporting_asset_ids": ["CAP-INVENTED"],
                }
            ],
            "audit": None,
            "expert_questions": [],
        }


def test_deep_research_rejects_route_asset_outside_snapshot() -> None:
    with pytest.raises(ValueError, match="outside the snapshot"):
        asyncio.run(
            generate_deep_research_stage(
                make_context("route_comparison"),
                make_snapshot(),
                InventedRouteClient(),
            )
        )


class MustNotCallClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise AssertionError("awaiting_expert stage must not call the model")


def test_expert_answer_returns_to_same_task_as_pending_finding() -> None:
    answer = ExpertAnswerInput(
        research_task_id="RESEARCH-001",
        question_id="Q-EXPERT-1",
        author_id="USER-1",
        author_name="王专家",
        message_url="https://example.test/message/1",
        answer_text="现场接口权限需要安全团队再次确认。",
    )
    context = make_context("awaiting_expert", expert_answers=[answer])

    result = asyncio.run(
        generate_deep_research_stage(context, make_snapshot(), MustNotCallClient())
    )

    assert result.status == "running"
    assert result.findings[0].boundary == "pending_confirmation"
    assert "王专家" in result.findings[0].text
    assert "https://example.test/message/1" in result.findings[0].text


def test_unsourced_research_finding_rejects_even_one_partial_source_id() -> None:
    with pytest.raises(ValueError, match="require asset_id and source_id"):
        ResearchFinding(
            finding_id="F1",
            text="AI 推断不能偷挂半个来源编号。",
            boundary="ai_inference",
            asset_id="CAP-001",
            source_id=None,
            stage="analysis",
        )
