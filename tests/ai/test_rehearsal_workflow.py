import asyncio
import json

from app.ai.rehearsal import (
    RehearsalAIWorkflow,
    evaluate_answer_rules,
    select_presentation_handoff,
)


class QueueClient:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.prompts: list[dict] = []

    async def generate_json(self, _system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append(json.loads(user_prompt))
        return self.responses.pop(0)


class SlowClient:
    async def generate_json(self, _system_prompt: str, _user_prompt: str) -> dict:
        await asyncio.sleep(0.05)
        return {}


def frozen_context() -> dict:
    return {
        "schema_version": "v2.0",
        "captured_at": "2026-08-12T00:00:00+00:00",
        "customer_profile": {
            "name": "示例客户",
            "profile": {"goals": ["缩短响应周期"], "concerns": ["数据权限"]},
        },
        "solution": {"result": {"summary": "先做小范围试点"}},
        "research": {"report": {"summary": "仍需核对案例"}},
        "intelligence_snapshot": {"items": [{"title": "公开招聘信息"}]},
        "response_matrix": {"items": [{"status": "missing_evidence"}]},
        "knowledge_gaps": ["三年案例仍待确认"],
        "boundary": "external intelligence is context, not enterprise capability evidence",
    }


def test_rule_fallback_builds_role_and_difficulty_specific_questions() -> None:
    asyncio.run(_assert_rule_fallback_questions())


async def _assert_rule_fallback_questions() -> None:
    workflow = RehearsalAIWorkflow()
    context = frozen_context()
    persona = await workflow.build_persona(
        context,
        role="technical_reviewer",
        difficulty="hard",
        focus_areas=["私有化部署", "数据权限"],
    )
    first, first_meta = await workflow.generate_question(
        context,
        persona=persona,
        role="technical_reviewer",
        difficulty="hard",
        focus_areas=["私有化部署", "数据权限"],
        sequence=1,
        history=[],
    )
    second, _ = await workflow.generate_question(
        context,
        persona=persona,
        role="technical_reviewer",
        difficulty="hard",
        focus_areas=["私有化部署", "数据权限"],
        sequence=2,
        history=[{"sequence": 1, "customer_question": first}],
    )
    assert persona["role_label"] == "技术评审"
    assert "私有化部署" in first
    assert "数据权限" in second
    assert first != second
    assert first_meta["mode"] == "rule_fallback"


def test_model_question_reads_frozen_context_and_history() -> None:
    asyncio.run(_assert_model_question_uses_context())


async def _assert_model_question_uses_context() -> None:
    client = QueueClient(
        {
            "question": "你刚才提到小范围试点，数据权限失败时如何止损？",
            "focus_area": "数据权限",
            "objection_type": "risk_boundary",
            "context_refs": ["customer_profile.profile.concerns", "solution.result.summary"],
            "asks_for_confirmation": False,
        }
    )
    workflow = RehearsalAIWorkflow(client)
    question, metadata = await workflow.generate_question(
        frozen_context(),
        persona={"role_label": "技术评审"},
        role="technical_reviewer",
        difficulty="standard",
        focus_areas=["数据权限"],
        sequence=2,
        history=[
            {
                "sequence": 1,
                "customer_question": "你们的数据权限依据是什么？",
                "employee_answer": "需要进一步确认。",
            }
        ],
    )
    prompt_input = client.prompts[0]["input"]
    assert "小范围试点" in question
    assert prompt_input["frozen_context"] == frozen_context()
    assert prompt_input["history"][0]["sequence"] == 1
    assert metadata["mode"] == "llm"


def test_invalid_schema_and_timeout_safely_fall_back() -> None:
    asyncio.run(_assert_invalid_and_timeout_fallback())


async def _assert_invalid_and_timeout_fallback() -> None:
    invalid = RehearsalAIWorkflow(QueueClient({"unexpected": "value"}))
    persona = await invalid.build_persona(
        frozen_context(),
        role="procurement",
        difficulty="standard",
        focus_areas=["预算"],
    )
    assert persona["generation"]["mode"] == "rule_fallback"
    assert persona["generation"]["fallback_reason"] == "schema_invalid"

    timeout = RehearsalAIWorkflow(SlowClient(), timeout_seconds=0.001)
    question, metadata = await timeout.generate_question(
        frozen_context(),
        persona=persona,
        role="procurement",
        difficulty="standard",
        focus_areas=["预算"],
        sequence=1,
        history=[],
    )
    assert question
    assert metadata["fallback_reason"] == "model_timeout"

    invented_ref = RehearsalAIWorkflow(
        QueueClient(
            {
                "question": "你们去年是不是已经为我们交付过同类系统？",
                "focus_area": "历史案例",
                "objection_type": "fabricated_fact",
                "context_refs": ["customer_profile.nonexistent_case"],
                "asks_for_confirmation": False,
            }
        )
    )
    question, metadata = await invented_ref.generate_question(
        frozen_context(),
        persona=persona,
        role="procurement",
        difficulty="standard",
        focus_areas=["历史案例"],
        sequence=1,
        history=[],
    )
    assert "已经为我们交付" not in question
    assert metadata["fallback_reason"] == "ungrounded_question"


def test_rule_guard_flags_promises_evidence_risk_and_gap() -> None:
    result = evaluate_answer_rules("我们保证百分之百成功，完全没有风险。", frozen_context())
    issue_types = {item["type"] for item in result["issues"]}
    assert result["score"] < 60
    assert {"overcommitment", "evidence_boundary", "pending_confirmation"} <= issue_types
    assert all("deduction" in item for item in result["issues"])


def test_report_is_traceable_and_handoff_stays_candidate_only() -> None:
    asyncio.run(_assert_report_is_traceable())


async def _assert_report_is_traceable() -> None:
    workflow = RehearsalAIWorkflow()
    context = frozen_context()
    context_before = json.loads(json.dumps(context, ensure_ascii=False))
    evaluation = evaluate_answer_rules(
        "根据能力库，建议先做试点；数据权限和案例需要确认，存在接口风险，下一步补材料。",
        context,
    )
    report = await workflow.generate_report(
        context,
        persona={"role_label": "客户决策人"},
        turns=[
            {
                "sequence": 1,
                "customer_question": "你们有什么依据？",
                "employee_answer": "先做试点。",
                "evaluation": evaluation,
            }
        ],
    )
    assert report["average_score"] == evaluation["score"]
    assert report["turn_details"][0]["sequence"] == 1
    assert report["context_version"]["schema_version"] == "v2.0"
    assert report["presentation_handoff"]["auto_applied"] is False
    assert report["presentation_handoff"]["status"] == "candidate_requires_employee_selection"
    assert "不是企业事实" in report["presentation_handoff"]["fact_boundary"]
    assert context == context_before

    selected = select_presentation_handoff(
        report, ["high_frequency_objections", "knowledge_gaps"]
    )
    assert selected["status"] == "employee_selected"
    assert set(selected["narrative_context"]) == {
        "high_frequency_objections",
        "knowledge_gaps",
    }
    assert selected["fact_ledger_writes"] == []
    assert selected["auto_applied"] is False
