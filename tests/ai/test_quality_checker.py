import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.quality import quality_check_solution
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
    SolutionContext,
    SourceReference,
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
        experiences=[
            RetrievedExperience(
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
                rank=1,
                match_reasons=["单线视觉试点匹配"],
                data=ExperienceDraft(
                    name="不改主系统的单产线视觉质检试点",
                    problem="降低人工图片复看。",
                    solution="旁路读取图片并由班组长确认。",
                    prerequisites="客户提供图像出口。",
                    result="历史项目完成单线试点验收。",
                    risks="不能外推全厂，也不能自动停线。",
                    source_id="SRC-EXP-001",
                ),
            )
        ],
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
                    limitations="不绕过权限，也不改善硬件成像。",
                    source_id="SRC-PRD-001",
                ),
            )
        ],
        missing_information=["相机型号"],
        created_at=datetime.now(UTC),
    )


def make_solution(historical_text: str = "历史项目完成了单线试点验收。") -> Solution:
    return Solution(
        requirement_understanding=[
            CitedItem(
                text="客户当前希望降低人工图片复看。",
                boundary=EvidenceBoundary.AI_INFERENCE,
            )
        ],
        initial_recommendations=[
            CitedItem(
                text="建议先做只读接入和人工确认的条件性试点。",
                boundary=EvidenceBoundary.AI_INFERENCE,
            )
        ],
        historical_evidence=[
            CitedItem(
                text=historical_text,
                boundary=EvidenceBoundary.HISTORICAL_FACT,
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
            )
        ],
        capability_composition=[
            CitedItem(
                text="已有能力可从获批出口只读接入图片。",
                boundary=EvidenceBoundary.ENTERPRISE_CAPABILITY,
                asset_id="CAP-001",
                source_id="SRC-PRD-001",
            )
        ],
        pending_confirmations=[
            CitedItem(
                text="相机型号仍待确认。",
                boundary=EvidenceBoundary.PENDING_CONFIRMATION,
            )
        ],
        sources=[
            SourceReference(
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
                title="不改主系统的单产线视觉质检试点",
            ),
            SourceReference(
                asset_id="CAP-001",
                source_id="SRC-PRD-001",
                title="工业图像只读接入与标准化",
            ),
        ],
    )


class AllSupportedQualityClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "没有参与报告生成" in system_prompt
        review_bundle = json.loads(
            user_prompt.split("--- 待质检论断开始 ---", 1)[1].split("--- 待质检论断结束 ---", 1)[0]
        )
        packets = review_bundle["claims"]
        historical_packet = next(
            packet for packet in packets if packet["boundary"] == "historical_fact"
        )
        assert historical_packet["evidence_key"] == "EXP-001/SRC-EXP-001"
        historical_evidence = review_bundle["asset_evidence"][historical_packet["evidence_key"]]
        assert historical_evidence["asset_id"] == "EXP-001"
        assert historical_evidence["content"]["result"] == ("历史项目完成单线试点验收。")
        return {
            "reviews": [
                {
                    "claim_id": packet["claim_id"],
                    "verdict": "supported",
                    "reason": "论断与给定证据和边界一致。",
                }
                for packet in packets
            ]
        }


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.result


def test_quality_check_passes_when_every_claim_is_supported() -> None:
    report = asyncio.run(
        quality_check_solution(
            make_context(),
            make_snapshot(),
            make_solution(),
            AllSupportedQualityClient(),
        )
    )

    assert report.passed is True
    assert report.requires_regeneration is False
    assert report.failed_claim_ids == []
    assert len(report.reviews) == 5


def test_quality_check_rejects_an_exaggerated_historical_claim() -> None:
    claim_ids = [
        "requirement_understanding:1",
        "initial_recommendations:1",
        "historical_evidence:1",
        "capability_composition:1",
        "pending_confirmations:1",
    ]
    result = {
        "reviews": [
            {
                "claim_id": claim_id,
                "verdict": ("unsupported" if claim_id == "historical_evidence:1" else "supported"),
                "reason": (
                    "历史资料只记录单线验收，不能外推为全厂必然成功。"
                    if claim_id == "historical_evidence:1"
                    else "论断有依据。"
                ),
            }
            for claim_id in claim_ids
        ]
    }

    report = asyncio.run(
        quality_check_solution(
            make_context(),
            make_snapshot(),
            make_solution("该经验证明当前客户全厂上线必然成功。"),
            StaticJsonModelClient(result),
        )
    )

    assert report.passed is False
    assert report.requires_regeneration is True
    assert report.failed_claim_ids == ["historical_evidence:1"]
    assert report.reviews[2].verdict == "unsupported"


@pytest.mark.parametrize(
    "reviews",
    [
        [],
        [
            {
                "claim_id": "requirement_understanding:1",
                "verdict": "supported",
                "reason": "只检查了一条。",
            }
        ],
        [
            {
                "claim_id": "fake:1",
                "verdict": "supported",
                "reason": "自造编号。",
            }
        ],
    ],
)
def test_quality_check_rejects_incomplete_or_invented_review_ids(reviews: list[dict]) -> None:
    with pytest.raises(ValueError, match="cover every claim once and in order"):
        asyncio.run(
            quality_check_solution(
                make_context(),
                make_snapshot(),
                make_solution(),
                StaticJsonModelClient({"reviews": reviews}),
            )
        )


def test_quality_check_rejects_citation_outside_snapshot() -> None:
    solution = make_solution()
    solution.historical_evidence[0] = CitedItem(
        text="另一个历史项目完成全厂上线。",
        boundary=EvidenceBoundary.HISTORICAL_FACT,
        asset_id="EXP-999",
        source_id="SRC-EXP-999",
    )
    solution.sources.append(
        SourceReference(
            asset_id="EXP-999",
            source_id="SRC-EXP-999",
            title="不存在于检索快照的资料",
        )
    )

    with pytest.raises(ValueError, match="is not in the retrieval snapshot"):
        asyncio.run(
            quality_check_solution(
                make_context(),
                make_snapshot(),
                solution,
                AllSupportedQualityClient(),
            )
        )
