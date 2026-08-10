from datetime import UTC, datetime

from app.ai.schemas import RetrievalSnapshot, SolutionContext
from app.services.ai_payload_adapter import (
    build_solution_context,
    normalize_retrieval_snapshot,
)


def test_adapter_converts_backend_payloads_to_ai_contracts() -> None:
    context = build_solution_context(
        "视觉质检",
        {
            "id": "profile-1",
            "customer_name": "制造客户",
            "status": "confirmed",
            "profile": {"industry": "制造", "background": "产线升级"},
        },
    )
    snapshot = normalize_retrieval_snapshot(
        {
            "experiences": [
                {
                    "id": "experience-1",
                    "source_id": "source-1",
                    "data": {
                        "name": "质检试点",
                        "problem": "人工复检压力",
                        "solution": "旁路部署",
                        "prerequisites": ["摄像设备", "缺陷样本"],
                        "risks": ["现场光照变化"],
                    },
                    "source_snapshot": {
                        "source_version": "3",
                        "reviewed_version": "3",
                        "permission_snapshot_id": "source-1:3:checked",
                        "permission_valid": True,
                        "available": True,
                    },
                }
            ],
            "capabilities": [
                {
                    "id": "capability-1",
                    "source_id": "source-2",
                    "data": {
                        "name": "缺陷识别",
                        "description": "识别产品缺陷",
                        "inputs": ["摄像数据"],
                        "outputs": ["缺陷结果"],
                        "limitations": ["需要现场校准", "依赖样本质量"],
                    },
                }
            ],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )

    validated_context = SolutionContext.model_validate(context)
    validated_snapshot = RetrievalSnapshot.model_validate(snapshot)

    assert validated_context.current_requirement == "视觉质检"
    assert validated_context.customer_profile.source_ids == ["profile-snapshot:profile-1"]
    assert validated_snapshot.experiences[0].rank == 1
    assert validated_snapshot.experiences[0].data.applicable_problem == "人工复检压力"
    assert validated_snapshot.experiences[0].data.prerequisites == "摄像设备；缺陷样本"
    assert snapshot["experiences"][0]["source_snapshot"]["reviewed_version"] == "3"
    assert validated_snapshot.capabilities[0].data.source_id == "source-2"
    assert validated_snapshot.capabilities[0].data.limitations == "需要现场校准；依赖样本质量"
