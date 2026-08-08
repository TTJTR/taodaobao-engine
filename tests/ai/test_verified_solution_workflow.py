import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.revision import revise_solution
from app.ai.pipelines.workflow import generate_verified_solution
from app.ai.schemas import (
    CapabilityDraft,
    ClaimReview,
    CustomerProfileDraft,
    QualityReport,
    RetrievalSnapshot,
    RetrievedCapability,
    SolutionContext,
)


def make_context() -> SolutionContext:
    return SolutionContext(
        customer_profile=CustomerProfileDraft(
            customer_name="星瀚精工集团",
            industry="离散制造",
            constraints=["不允许自动停线"],
            information_gaps=["相机型号"],
            profile_summary="客户希望做人工确认的单线视觉试点。",
            source_ids=["SRC-CUST-001"],
        ),
        current_requirement="降低人工图片复看。",
    )


def make_snapshot() -> RetrievalSnapshot:
    return RetrievalSnapshot(
        capabilities=[
            RetrievedCapability(
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
                    limitations="不绕过权限，也不自动停线。",
                    source_id="SRC-PRD-001",
                ),
            )
        ],
        missing_information=["相机型号"],
        created_at=datetime.now(UTC),
    )


def model_solution_result(recommendation: str) -> dict:
    return {
        "requirement_understanding": [
            {
                "text": "客户希望降低人工图片复看。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [
            {
                "text": recommendation,
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "historical_evidence": [],
        "capability_composition": [
            {
                "text": "已有能力可从获批出口只读接入图片。",
                "boundary": "enterprise_capability",
                "asset_id": "CAP-001",
                "source_id": "SRC-PRD-001",
            }
        ],
        "prerequisites_and_risks": [],
        "pending_confirmations": [],
        "suggested_questions": ["现有相机型号是什么？"],
    }


def make_failed_quality_report() -> QualityReport:
    return QualityReport(
        reviews=[
            ClaimReview(
                claim_id="initial_recommendations:1",
                verdict="unsupported",
                reason="资料明确不支持自动停线。",
            )
        ],
        passed=False,
        requires_regeneration=True,
        failed_claim_ids=["initial_recommendations:1"],
        summary="有 1 条论断需要修改。",
    )


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.call_count = 0

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.call_count += 1
        return self.result


class RevisionClient:
    def __init__(self, corrected_result: dict) -> None:
        self.corrected_result = corrected_result
        self.call_count = 0

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.call_count += 1
        assert "任务是依据质检意见重写整份报告" in system_prompt
        assert "资料明确不支持自动停线" in user_prompt
        assert "建议系统自动停线" in user_prompt
        return self.corrected_result


class SequenceQualityClient:
    def __init__(self, failing_calls: int) -> None:
        self.failing_calls = failing_calls
        self.call_count = 0

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.call_count += 1
        review_bundle = json.loads(
            user_prompt.split("--- 待质检论断开始 ---", 1)[1].split("--- 待质检论断结束 ---", 1)[0]
        )
        packets = review_bundle["claims"]
        reviews = []
        for packet in packets:
            fails = (
                self.call_count <= self.failing_calls
                and packet["claim_id"] == "initial_recommendations:1"
            )
            reviews.append(
                {
                    "claim_id": packet["claim_id"],
                    "verdict": "unsupported" if fails else "supported",
                    "reason": ("资料明确不支持自动停线。" if fails else "论断符合证据与边界。"),
                }
            )
        return {"reviews": reviews}


def test_revise_solution_uses_feedback_and_rebuilds_sources() -> None:
    context = make_context()
    snapshot = make_snapshot()
    initial_result = model_solution_result("建议系统自动停线。")
    initial_solution_client = StaticJsonModelClient(initial_result)
    corrected_result = model_solution_result("建议只读接入，异常交由人工确认。")
    revision_client = RevisionClient(corrected_result)

    from app.ai.pipelines.solution import generate_solution

    initial_solution = asyncio.run(generate_solution(context, snapshot, initial_solution_client))
    revised = asyncio.run(
        revise_solution(
            context,
            snapshot,
            initial_solution,
            make_failed_quality_report(),
            revision_client,
        )
    )

    assert revised.initial_recommendations[0].text == ("建议只读接入，异常交由人工确认。")
    assert [(source.asset_id, source.source_id) for source in revised.sources] == [
        ("CAP-001", "SRC-PRD-001")
    ]


def test_revise_solution_rejects_an_already_passed_report() -> None:
    context = make_context()
    snapshot = make_snapshot()
    from app.ai.pipelines.solution import generate_solution

    solution = asyncio.run(
        generate_solution(
            context,
            snapshot,
            StaticJsonModelClient(model_solution_result("建议人工确认。")),
        )
    )
    passed_report = QualityReport(
        reviews=[
            ClaimReview(
                claim_id="initial_recommendations:1",
                verdict="supported",
                reason="已有依据。",
            )
        ],
        passed=True,
        requires_regeneration=False,
        failed_claim_ids=[],
        summary="通过。",
    )

    with pytest.raises(ValueError, match="does not need revision"):
        asyncio.run(
            revise_solution(
                context,
                snapshot,
                solution,
                passed_report,
                StaticJsonModelClient(model_solution_result("不会调用")),
            )
        )


def test_workflow_stops_after_first_quality_pass() -> None:
    generation_client = StaticJsonModelClient(model_solution_result("建议人工确认。"))
    quality_client = SequenceQualityClient(failing_calls=0)
    revision_client = RevisionClient(model_solution_result("不会调用"))

    result = asyncio.run(
        generate_verified_solution(
            make_context(),
            make_snapshot(),
            generation_client,
            quality_client,
            revision_client,
        )
    )

    assert result.passed is True
    assert result.revision_count == 0
    assert len(result.attempts) == 1
    assert revision_client.call_count == 0


def test_workflow_revises_once_then_passes() -> None:
    generation_client = StaticJsonModelClient(model_solution_result("建议系统自动停线。"))
    quality_client = SequenceQualityClient(failing_calls=1)
    revision_client = RevisionClient(model_solution_result("建议只读接入，异常交由人工确认。"))

    result = asyncio.run(
        generate_verified_solution(
            make_context(),
            make_snapshot(),
            generation_client,
            quality_client,
            revision_client,
        )
    )

    assert result.passed is True
    assert result.revision_count == 1
    assert len(result.attempts) == 2
    assert result.attempts[0].quality_report.passed is False
    assert result.attempts[1].quality_report.passed is True
    assert revision_client.call_count == 1


def test_workflow_stops_after_two_failed_revisions() -> None:
    bad_result = model_solution_result("建议系统自动停线。")
    generation_client = StaticJsonModelClient(bad_result)
    quality_client = SequenceQualityClient(failing_calls=3)
    revision_client = RevisionClient(bad_result)

    result = asyncio.run(
        generate_verified_solution(
            make_context(),
            make_snapshot(),
            generation_client,
            quality_client,
            revision_client,
        )
    )

    assert result.passed is False
    assert result.revision_count == 2
    assert len(result.attempts) == 3
    assert revision_client.call_count == 2


@pytest.mark.parametrize("max_revisions", [-1, 3])
def test_workflow_rejects_invalid_revision_limit(max_revisions: int) -> None:
    with pytest.raises(ValueError, match="max_revisions must be between 0 and 2"):
        asyncio.run(
            generate_verified_solution(
                make_context(),
                make_snapshot(),
                StaticJsonModelClient(model_solution_result("建议人工确认。")),
                SequenceQualityClient(failing_calls=0),
                StaticJsonModelClient(model_solution_result("不会调用")),
                max_revisions=max_revisions,
            )
        )
