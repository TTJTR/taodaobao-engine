import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.db.models import CollaborationStatus
from app.services.expert_collaboration_service import ExpertCollaborationService


def _context() -> dict:
    return {
        "customer_profile": {
            "customer_name": "华星制造",
            "profile": {"profile_summary": "希望在不中断产线的前提下验证质检方案。"},
        },
        "research_title": "质检试点研究",
        "research_question": "如何完成低风险质检试点？",
        "research_stage": "waiting_expert",
        "research_document": {"summary": "建议先采用旁路接入并人工复核。"},
        "evidence_snapshot": {
            "experiences": [{"asset_id": "EXP-1", "data": {"name": "旁路试点经验"}}],
            "capabilities": [{"asset_id": "CAP-1", "data": {"name": "旁路接入能力"}}],
        },
        "knowledge_gaps": ["接口权限负责人待确认"],
    }


def test_default_group_name_uses_customer_and_research_topic() -> None:
    assert ExpertCollaborationService._default_group_name(
        _context(), "质检试点研究"
    ) == "【淘到宝】与华星制造的质检试点研究协作"


def test_opening_message_contains_business_summary_document_and_trust_boundary() -> None:
    service = object.__new__(ExpertCollaborationService)
    item = SimpleNamespace(
        research_task_id=uuid.uuid4(),
        context_snapshot=_context(),
        feishu_document_url="https://example.feishu.cn/docx/research",
        group_name="【淘到宝】与华星制造的质检试点研究协作",
        questions=[{"question_id": "GAP-1", "question": "接口权限由谁审批？"}],
    )

    bundle = service._content_bundle(item)
    opening = bundle["group_opening"]["text"]

    assert "华星制造" in opening
    assert "如何完成低风险质检试点" in opening
    assert "旁路试点经验" in opening
    assert "接口权限负责人待确认" in opening
    assert "https://example.feishu.cn/docx/research" in opening
    assert "pending_confirmation" in opening
    assert str(item.research_task_id) in opening


@pytest.mark.asyncio
async def test_confirm_rejects_empty_expert_selection_before_feishu_side_effects() -> None:
    service = object.__new__(ExpertCollaborationService)
    item = SimpleNamespace(
        id=uuid.uuid4(),
        research_task_id=uuid.uuid4(),
        status=CollaborationStatus.AWAITING_CONFIRMATION,
        selected_expert_ids=[],
        questions=[{"question_id": "GAP-1", "question": "由谁审批？"}],
    )
    service.get = AsyncMock(return_value=item)
    service._lock_task = AsyncMock()
    service.feishu = AsyncMock()

    with pytest.raises(AppError, match="请先确认专家和问题") as exc_info:
        await service.confirm(item.id)

    assert exc_info.value.status_code == 422
    service.feishu.create_group.assert_not_awaited()
