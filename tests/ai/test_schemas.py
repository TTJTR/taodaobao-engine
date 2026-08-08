from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ai.schemas import (
    CapabilityDraft,
    CitedItem,
    CustomerProfileDraft,
    EvidenceBoundary,
    ExperienceDraft,
    RetrievalSnapshot,
    RetrievedCapability,
    RetrievedExperience,
    Solution,
    SourceReference,
)


def make_experience(source_id: str = "SRC-EXP-001") -> ExperienceDraft:
    return ExperienceDraft(
        name="不改主系统的单产线视觉质检试点",
        problem="降低人工图片复看压力。",
        solution="旁路读取图片并由班组长确认异常。",
        prerequisites="客户允许只读接入。",
        risks="不能自动停线或修改工艺参数。",
        source_id=source_id,
    )


def make_capability(source_id: str = "SRC-PRD-001") -> CapabilityDraft:
    return CapabilityDraft(
        name="工业图像只读接入",
        description="读取获批的相机流并补齐元数据。",
        input="相机流、读取凭证和批次标识。",
        output="标准图像记录和读取失败日志。",
        limitations="不绕过权限，也不改善硬件成像质量。",
        source_id=source_id,
    )


def test_customer_profile_matches_expected_dataset_shape() -> None:
    profile = CustomerProfileDraft(
        customer_name="星瀚精工集团",
        industry="离散制造",
        background="客户希望先做一条线的质量数字化试点。",
        current_problems=["人工图片复看占用质量人员"],
        goals=["减少人工复看压力"],
        constraints=["图片不得离开园区", "不允许 AI 自动停线"],
        existing_systems=["MES 有查询接口"],
        information_gaps=["相机型号和图像质量"],
        profile_status="confirmed",
        profile_summary="客户拟在 A3 单线做人工确认的旁路试点。",
        source_ids=["SRC-CUST-001"],
    )

    assert profile.current_problems == ["人工图片复看占用质量人员"]
    assert profile.source_ids == ["SRC-CUST-001"]
    assert profile.model_dump(mode="json")["profile_status"] == "confirmed"


def test_customer_profile_rejects_duplicate_sources() -> None:
    with pytest.raises(ValidationError, match="source_ids must be unique"):
        CustomerProfileDraft(
            customer_name="星瀚精工集团",
            profile_summary="单线试点。",
            source_ids=["SRC-CUST-001", "SRC-CUST-001"],
        )


def test_retrieval_snapshot_enforces_top_k_limits() -> None:
    experiences = [
        RetrievedExperience(
            asset_id=f"EXP-{index}",
            source_id=f"SRC-EXP-{index}",
            rank=index,
            match_reasons=["问题和约束相符"],
            data=make_experience(f"SRC-EXP-{index}"),
        )
        for index in range(1, 5)
    ]

    with pytest.raises(ValidationError, match="at most 3 items"):
        RetrievalSnapshot(experiences=experiences, created_at=datetime.now(UTC))


def test_retrieved_asset_source_must_match_embedded_data() -> None:
    with pytest.raises(ValidationError, match="source_id must match data.source_id"):
        RetrievedCapability(
            asset_id="CAP-001",
            source_id="SRC-PRD-001",
            rank=1,
            match_reasons=["场景匹配"],
            data=make_capability("SRC-PRD-OTHER"),
        )


def test_historical_fact_requires_asset_and_source_ids() -> None:
    with pytest.raises(ValidationError, match="require asset_id and source_id"):
        CitedItem(
            text="历史项目将异常处置时间缩短。",
            boundary=EvidenceBoundary.HISTORICAL_FACT,
        )


def test_ai_inference_must_not_claim_an_enterprise_source() -> None:
    with pytest.raises(ValidationError, match="must not cite enterprise sources"):
        CitedItem(
            text="建议先做单线试点。",
            boundary=EvidenceBoundary.AI_INFERENCE,
            asset_id="CAP-001",
            source_id="SRC-PRD-001",
        )


def test_solution_rejects_citation_missing_from_sources_section() -> None:
    with pytest.raises(ValidationError, match="cited sources are missing"):
        Solution(
            historical_evidence=[
                CitedItem(
                    text="历史项目采用单线旁路试点。",
                    boundary=EvidenceBoundary.HISTORICAL_FACT,
                    asset_id="EXP-001",
                    source_id="SRC-EXP-001",
                )
            ]
        )


def test_solution_accepts_citation_listed_in_sources_section() -> None:
    solution = Solution(
        historical_evidence=[
            CitedItem(
                text="历史项目采用单线旁路试点。",
                boundary=EvidenceBoundary.HISTORICAL_FACT,
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
            )
        ],
        sources=[
            SourceReference(
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
                title="云栖零部件单线视觉试点复盘",
            )
        ],
        suggested_questions=["现有相机型号是什么？"],
    )

    assert solution.historical_evidence[0].asset_id == "EXP-001"
