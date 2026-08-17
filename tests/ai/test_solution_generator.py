import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.solution import generate_solution
from app.ai.schemas import (
    CapabilityDraft,
    CustomerProfileDraft,
    EvidenceConflict,
    ExperienceDraft,
    RetrievalSnapshot,
    RetrievedCapability,
    RetrievedExperience,
    SolutionContext,
)


def make_context() -> SolutionContext:
    return SolutionContext(
        customer_profile=CustomerProfileDraft(
            customer_name="星瀚精工集团",
            industry="离散制造",
            current_problems=["人工图片复看压力大"],
            goals=["减少人工复看"],
            constraints=["不替换现有 MES", "图片不出园区", "不允许自动停线"],
            information_gaps=["相机型号", "试点验收指标"],
            profile_summary="客户希望做园区内、人工确认的单线视觉试点。",
            source_ids=["SRC-CUST-001"],
        ),
        current_requirement="三个月内在 A3 线降低人工图片复看。",
    )


def make_snapshot(can_generate_solution: bool = True) -> RetrievalSnapshot:
    experience = RetrievedExperience(
        asset_id="EXP-001",
        source_id="SRC-EXP-001",
        rank=1,
        match_reasons=["单线试点与旁路约束匹配"],
        data=ExperienceDraft(
            name="不改主系统的单产线视觉质检试点",
            problem="在不修改主系统的条件下降低人工复看。",
            solution="旁路读取图片，异常由班组长确认。",
            prerequisites="客户提供图像出口。",
            result="历史项目完成单线试点验收。",
            risks="不能外推到全厂，也不能自动停线。",
            source_id="SRC-EXP-001",
        ),
    )
    capability = RetrievedCapability(
        asset_id="CAP-001",
        source_id="SRC-PRD-001",
        rank=1,
        match_reasons=["支持只读图片接入"],
        data=CapabilityDraft(
            name="工业图像只读接入与标准化",
            description="从获批出口读取图片并补齐元数据。",
            input="图片和读取凭证。",
            output="标准图像记录。",
            prerequisites="客户提供合法数据出口。",
            limitations="不绕过权限，也不改善硬件成像。",
            source_id="SRC-PRD-001",
        ),
    )
    return RetrievalSnapshot(
        experiences=[experience],
        capabilities=[capability],
        missing_information=["边缘服务器规格"],
        can_generate_solution=can_generate_solution,
        created_at=datetime.now(UTC),
    )


def valid_model_result() -> dict:
    return {
        "requirement_understanding": [
            {
                "text": "客户希望先做 A3 单线试点。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [
            {
                "text": "建议先做只读接入和人工确认的条件性试点。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "historical_evidence": [
            {
                "text": "历史项目采用不改主系统的单线旁路做法。",
                "boundary": "historical_fact",
                "asset_id": "EXP-001",
                "source_id": "SRC-EXP-001",
            }
        ],
        "capability_composition": [
            {
                "text": "已有能力可从获批出口只读接入图片。",
                "boundary": "enterprise_capability",
                "asset_id": "CAP-001",
                "source_id": "SRC-PRD-001",
            }
        ],
        "prerequisites_and_risks": [
            {
                "text": "需要客户提供合法的图片数据出口。",
                "boundary": "enterprise_capability",
                "asset_id": "CAP-001",
                "source_id": "SRC-PRD-001",
            }
        ],
        "pending_confirmations": [
            {
                "text": "真实样本量待确认。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "suggested_questions": ["现有相机型号是什么？"],
    }


class StubSolutionClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "检索快照是本轮唯一企业依据" in system_prompt
        assert "EXP-001" in user_prompt
        assert "CAP-001" in user_prompt
        return valid_model_result()


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.result


def test_generate_solution_builds_sources_and_preserves_missing_information() -> None:
    solution = asyncio.run(generate_solution(make_context(), make_snapshot(), StubSolutionClient()))

    assert {(source.asset_id, source.source_id) for source in solution.sources} == {
        ("EXP-001", "SRC-EXP-001"),
        ("CAP-001", "SRC-PRD-001"),
    }
    pending_text = [item.text for item in solution.pending_confirmations]
    assert "相机型号" in pending_text
    assert "试点验收指标" in pending_text
    assert "边缘服务器规格" in pending_text


def test_generate_solution_repairs_unknown_boundary_labels_by_section() -> None:
    result = valid_model_result()
    result["requirement_understanding"][0]["boundary"] = "customer_requirement"
    result["historical_evidence"][0]["boundary"] = "case_evidence"
    result["capability_composition"][0]["boundary"] = "product_ability"
    result["pending_confirmations"][0]["boundary"] = "needs_confirmation"

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    assert solution.requirement_understanding[0].boundary == "pending_confirmation"
    assert solution.historical_evidence[0].boundary == "historical_fact"
    assert solution.capability_composition[0].boundary == "enterprise_capability"
    assert solution.pending_confirmations[0].boundary == "pending_confirmation"


def test_generate_solution_conservatively_repairs_bare_string_items() -> None:
    result = valid_model_result()
    result["initial_recommendations"] = ["先验证一条产线"]
    result["pending_confirmations"] = ["MES 接口权限待确认"]
    result["historical_evidence"] = ["未经结构化引用的历史描述"]

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    assert solution.initial_recommendations[0].boundary == "ai_inference"
    assert solution.pending_confirmations[0].boundary == "pending_confirmation"
    assert not solution.historical_evidence
    moved = next(
        item for item in solution.pending_confirmations if item.text == "未经结构化引用的历史描述"
    )
    assert moved.boundary == "pending_confirmation"
    assert moved.asset_id is None


def test_generate_solution_keeps_uncited_pending_requirement_as_pending() -> None:
    result = valid_model_result()
    result["requirement_understanding"][0].update(
        {"text": "三个月是否足够仍待确认。", "boundary": "unknown_label"}
    )

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    assert solution.requirement_understanding[0].boundary == "pending_confirmation"


def test_generate_solution_marks_numeric_customer_requirement_pending() -> None:
    result = valid_model_result()
    result["requirement_understanding"][0].update(
        {"text": "客户希望在90天内完成试点", "boundary": "ai_inference"}
    )

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    assert solution.requirement_understanding[0].boundary == "pending_confirmation"


def test_generate_solution_removes_customer_source_from_requirement_understanding() -> None:
    result = valid_model_result()
    result["requirement_understanding"][0].update(
        {
            "boundary": "customer_fact",
            "asset_id": None,
            "source_id": "SRC-CUST-001",
        }
    )

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    item = solution.requirement_understanding[0]
    assert item.boundary == "pending_confirmation"
    assert item.asset_id is None
    assert item.source_id is None


def test_generate_solution_completes_a_unique_missing_asset_id() -> None:
    result = valid_model_result()
    result["historical_evidence"][0].update(
        {"boundary": "historical_fact", "asset_id": None, "source_id": "SRC-EXP-001"}
    )

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    assert solution.historical_evidence[0].asset_id == "EXP-001"
    assert solution.historical_evidence[0].source_id == "SRC-EXP-001"


def test_generate_solution_rejects_invented_citation() -> None:
    result = valid_model_result()
    result["capability_composition"][0].update({"asset_id": "CAP-999", "source_id": "SRC-PRD-999"})

    with pytest.raises(ValueError, match="not in the retrieval snapshot"):
        asyncio.run(
            generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
        )


def test_generate_solution_rejects_one_claim_that_mentions_another_asset() -> None:
    result = valid_model_result()
    result["historical_evidence"][0]["text"] = (
        "EXP-001 和 CAP-001 都证明当前路线已经在历史项目中完成验收。"
    )

    with pytest.raises(ValueError, match="must not mention another asset id"):
        asyncio.run(
            generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
        )


def test_generate_solution_rejects_uncited_inference_that_mentions_enterprise_asset() -> None:
    result = valid_model_result()
    result["initial_recommendations"][0]["text"] = "建议直接采用 CAP-001。"

    with pytest.raises(ValueError, match="uncited items must not mention"):
        asyncio.run(
            generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
        )


def test_generate_solution_rejects_wrong_evidence_boundary() -> None:
    result = valid_model_result()
    result["capability_composition"][0].update(
        {
            "boundary": "enterprise_capability",
            "asset_id": "EXP-001",
            "source_id": "SRC-EXP-001",
        }
    )

    with pytest.raises(ValueError, match="wrong evidence boundary"):
        asyncio.run(
            generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
        )


def test_generate_solution_removes_source_from_ai_inference() -> None:
    result = valid_model_result()
    result["initial_recommendations"][0].update({"asset_id": "CAP-001", "source_id": "SRC-PRD-001"})

    solution = asyncio.run(
        generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
    )

    item = solution.initial_recommendations[0]
    assert item.boundary == "ai_inference"
    assert item.asset_id is None
    assert item.source_id is None


def test_configured_opening_line_appears_exactly_once() -> None:
    context = make_context().model_copy(update={"opening_line": "先让证据开口。"})
    result = valid_model_result()
    result["initial_recommendations"].append(
        {
            "text": "先让证据开口。",
            "boundary": "ai_inference",
            "asset_id": None,
            "source_id": None,
        }
    )

    solution = asyncio.run(
        generate_solution(context, make_snapshot(), StaticJsonModelClient(result))
    )

    all_text = [
        item.text
        for section in (
            solution.requirement_understanding,
            solution.initial_recommendations,
            solution.historical_evidence,
            solution.capability_composition,
            solution.prerequisites_and_risks,
            solution.pending_confirmations,
        )
        for item in section
    ]
    assert all_text.count("先让证据开口。") == 1


def test_explicit_evidence_conflict_always_becomes_pending_question() -> None:
    snapshot = make_snapshot().model_copy(
        update={
            "conflicts": [
                EvidenceConflict(
                    description="历史做法与当前能力资料对写入权限描述冲突。",
                    asset_ids=["EXP-001", "CAP-001"],
                    source_ids=["SRC-EXP-001", "SRC-PRD-001"],
                    clarification_question="当前项目到底是否允许写回主系统？",
                )
            ]
        }
    )

    solution = asyncio.run(
        generate_solution(make_context(), snapshot, StaticJsonModelClient(valid_model_result()))
    )

    pending_texts = [item.text for item in solution.pending_confirmations]
    assert "当前项目到底是否允许写回主系统？" in pending_texts


def test_generate_solution_adds_no_basis_notice() -> None:
    result = valid_model_result()

    solution = asyncio.run(
        generate_solution(
            make_context(),
            make_snapshot(can_generate_solution=False),
            StaticJsonModelClient(result),
        )
    )

    assert solution.requirement_understanding[0].text == (
        "当前知识库无足够依据，不能按当前要求生成正式方案。"
    )


def test_generate_solution_rejects_model_supplied_sources() -> None:
    result = valid_model_result()
    result["sources"] = []

    with pytest.raises(ValueError, match="required solution fields"):
        asyncio.run(
            generate_solution(make_context(), make_snapshot(), StaticJsonModelClient(result))
        )
