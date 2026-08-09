import asyncio
from datetime import UTC, datetime
from typing import Any

from app.ai.engine import BailianAIEngine, MockAIEngine
from app.ai.schemas import (
    CapabilityDraft,
    CustomerProfileDraft,
    ExperienceDraft,
    SearchIntent,
    Solution,
)


class SequenceModelClient:
    model_version = "test-model-v1"
    parameter_version = "temperature=0"
    last_attempt_count = 1

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.prompts: list[str] = []

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.prompts.append(user_prompt)
        return self.results.pop(0)


def make_context() -> dict[str, Any]:
    return {
        "customer_profile": {
            "customer_name": "测试客户",
            "industry": "制造业",
            "background": None,
            "current_problems": ["人工处理慢"],
            "goals": ["提升处理效率"],
            "constraints": ["必须人工确认"],
            "existing_systems": [],
            "information_gaps": ["验收指标"],
            "profile_status": "pending_confirmation",
            "profile_summary": "客户希望提升效率，但必须人工确认。",
            "source_ids": ["SRC-001"],
        },
        "current_requirement": "先做一个小范围试点。",
        "conversation_history": [],
    }


def make_snapshot() -> dict[str, Any]:
    return {
        "experiences": [],
        "capabilities": [],
        "missing_information": [],
        "gap_summary": "暂无可用企业证据",
        "can_generate_solution": False,
        "created_at": datetime.now(UTC).isoformat(),
    }


def make_populated_snapshot() -> dict[str, Any]:
    return {
        "experiences": [
            {
                "asset_id": "EXP-001",
                "source_id": "SRC-EXP-001",
                "rank": 1,
                "match_reasons": ["问题相似"],
                "data": {
                    "name": "旁路试点经验",
                    "applicable_problem": "在不中断生产的前提下验证方案",
                    "solution": "先旁路接入并由人工复核结果。",
                    "source_id": "SRC-EXP-001",
                },
            }
        ],
        "capabilities": [
            {
                "asset_id": "CAP-001",
                "source_id": "SRC-CAP-001",
                "rank": 1,
                "match_reasons": ["能力匹配"],
                "data": {
                    "name": "旁路接入",
                    "description": "在不替换核心系统的情况下接入分析能力。",
                    "source_id": "SRC-CAP-001",
                },
            }
        ],
        "missing_information": [],
        "can_generate_solution": True,
        "created_at": datetime.now(UTC).isoformat(),
    }


def test_bailian_engine_repairs_invalid_profile_structure_once() -> None:
    invalid = {"customer_name": "测试客户"}
    valid = {
        "customer_name": "测试客户",
        "industry": "制造业",
        "background": None,
        "current_problems": ["人工处理慢"],
        "goals": ["提升效率"],
        "constraints": ["必须人工确认"],
        "existing_systems": [],
        "information_gaps": ["验收指标"],
        "profile_status": "pending_confirmation",
        "profile_summary": "客户希望提升效率。",
        "source_ids": ["SRC-001"],
    }
    client = SequenceModelClient([invalid, valid])
    engine = BailianAIEngine(client)  # type: ignore[arg-type]

    result = asyncio.run(engine.extract_profile("会议纪要", ["SRC-001"]))

    CustomerProfileDraft.model_validate(result)
    assert len(client.prompts) == 2
    assert "上一次输出未通过结构校验" in client.prompts[1]
    assert engine.last_run is not None
    assert engine.last_run.status == "succeeded"
    assert engine.last_run.structure_repair_count == 1
    assert engine.last_run.model_version == "test-model-v1"


def test_mock_engine_implements_all_five_frozen_outputs() -> None:
    engine = MockAIEngine()

    profile = asyncio.run(engine.extract_profile("会议纪要", ["SRC-001"]))
    experience = asyncio.run(engine.extract_experience("项目复盘", "SRC-EXP-001"))
    capabilities = asyncio.run(engine.extract_capabilities("产品说明", "SRC-PRD-001"))
    intent = asyncio.run(engine.extract_search_intent(make_context()))
    solution = asyncio.run(engine.generate_solution(make_context(), make_snapshot()))

    CustomerProfileDraft.model_validate(profile)
    ExperienceDraft.model_validate(experience)
    assert capabilities
    CapabilityDraft.model_validate(capabilities[0])
    SearchIntent.model_validate(intent)
    Solution.model_validate(solution)
    assert len(engine.recorder.records) == 5
    assert {record.method for record in engine.recorder.records} == {
        "extract_profile",
        "extract_experience",
        "extract_capabilities",
        "extract_search_intent",
        "generate_solution",
    }


def test_mock_solution_refuses_to_invent_evidence_for_empty_snapshot() -> None:
    result = asyncio.run(MockAIEngine().generate_solution(make_context(), make_snapshot()))
    solution = Solution.model_validate(result)

    assert not solution.historical_evidence
    assert not solution.capability_composition
    assert "无足够依据" in solution.requirement_understanding[0].text


def test_mock_solution_renders_every_retrieved_asset_in_matching_section() -> None:
    result = asyncio.run(
        MockAIEngine().generate_solution(make_context(), make_populated_snapshot())
    )
    solution = Solution.model_validate(result)

    assert [item.asset_id for item in solution.historical_evidence] == ["EXP-001"]
    assert [item.asset_id for item in solution.capability_composition] == ["CAP-001"]
    assert {source.asset_id for source in solution.sources} == {"EXP-001", "CAP-001"}
    assert solution.initial_recommendations
    assert solution.prerequisites_and_risks


def test_bailian_engine_deep_stage_uses_shared_trace_and_stage_metadata() -> None:
    client = SequenceModelClient(
        [
            {
                "research_plan": {
                    "objective": "比较试点路线",
                    "subquestions": [
                        {
                            "question_id": "Q1",
                            "question": "有哪些有证据的路线？",
                            "completion_condition": "每条路线有证据或明确缺口",
                            "status": "pending",
                        }
                    ],
                    "completion_conditions": ["完成路线与证据核验"],
                },
                "findings": [],
                "routes": [],
                "audit": None,
                "expert_questions": [],
            }
        ]
    )
    engine = BailianAIEngine(client)  # type: ignore[arg-type]
    context = {
        **make_context(),
        "mode": "deep",
        "stage": "planning",
        "trace_id": "TRACE-SHARED-001",
        "research_task_id": "RESEARCH-001",
    }

    result = asyncio.run(engine.generate_solution(context, make_snapshot()))

    assert result["trace_id"] == "TRACE-SHARED-001"
    assert result["stage"] == "planning"
    assert engine.last_run is not None
    assert engine.last_run.trace_id == "TRACE-SHARED-001"
    assert engine.last_run.stage == "deep_research.planning"
    assert engine.last_run.prompt_version == "deep-research-v1"
