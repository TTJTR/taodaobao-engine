import asyncio

import pytest
from pydantic import ValidationError

from app.ai.pipelines.extractor import (
    MAX_RAW_TEXT_CHARACTERS,
    extract_capabilities_from_text,
    extract_experience_from_text,
    extract_profile_from_text,
)


class StubJsonModelClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "只能使用原文明确写出的事实" in system_prompt
        assert "SRC-EXP-001" in user_prompt
        return {
            "name": "不改主系统的单产线视觉质检试点",
            "problem": "在不修改主系统的条件下降低人工复看。",
            "solution": "只读旁路接入图片，异常交给班组长确认。",
            "prerequisites": "客户提供图像流和批次查询接口。",
            "result": "仅完成 B2 单线试点验收。",
            "risks": "不能外推到全厂，也不能自动停线。",
            "follow_up_foundation": "保留缺陷口径和适配器配置。",
            "applicable_conditions": "允许旁路接入和人工确认的单线试点。",
            "tags": ["视觉质检", "旁路接入", "人工确认"],
            "source_id": "模型乱写的来源",
            "embedding_text": "模型乱写的检索文本",
        }


class StubProfileModelClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "不能补充常识或猜测" in system_prompt
        assert "SRC-CUST-001" in user_prompt
        return {
            "customer_name": "星瀚精工集团",
            "industry": "离散制造",
            "background": "客户希望在 A3 线做质量数字化试点。",
            "current_problems": ["人工复看压力大"],
            "goals": ["减少人工复看"],
            "constraints": ["图片不出园区", "不允许 AI 自动停线"],
            "existing_systems": ["MES 有查询接口"],
            "information_gaps": ["相机型号", "验收指标"],
            "profile_status": "confirmed",
            "profile_summary": "客户希望做园区内、人工确认的单线试点。",
            "source_ids": ["模型乱写的来源"],
        }


class StubCapabilityModelClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "灰度、试验、待审核能力都要提取" in system_prompt
        assert "SRC-PRD-001" in user_prompt
        return {
            "capabilities": [
                {
                    "name": "工业图像只读接入与标准化",
                    "description": "从获批数据出口读取图片并补齐元数据。",
                    "input": "图片、读取凭证和批次标识。",
                    "output": "标准图像记录和失败日志。",
                    "prerequisites": "客户提供合法数据出口和只读权限。",
                    "limitations": "不绕过权限，也不改善镜头和光源。",
                    "dependencies": [],
                    "tags": ["视觉质检", "只读接入"],
                    "source_id": "模型乱写的来源",
                    "embedding_text": "模型乱写的检索文本",
                },
                {
                    "name": "图像可用性预提示",
                    "description": "对模糊、过暗或过曝图片给出测试提示。",
                    "input": "待检查图片。",
                    "output": "可能不可用标记和简单原因。",
                    "prerequisites": "仅在测试环境启用。",
                    "limitations": "规则尚未冻结，不能作为正式质量判断。",
                    "dependencies": ["工业图像只读接入与标准化"],
                    "tags": ["图像质量", "灰度能力", "待校验"],
                    "source_id": "模型乱写的来源",
                    "embedding_text": "模型乱写的检索文本",
                },
            ]
        }


class StaticJsonModelClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return self.result


class MultiSourceProfileClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "customer_name": "测试客户",
            "industry": "制造业",
            "background": None,
            "current_problems": [],
            "goals": ["三个月完成试点"],
            "constraints": ["数据不出园区"],
            "existing_systems": [],
            "information_gaps": [],
            "profile_status": "confirmed",
            "profile_summary": "客户希望三个月完成园区内试点。",
            "source_ids": ["模型乱写"],
            "fact_sources": [
                {
                    "field": "goals",
                    "value": "三个月完成试点",
                    "source_id": "SRC-MEETING",
                    "quote": "客户：希望三个月完成试点",
                    "speaker_role": "customer",
                },
                {
                    "field": "constraints",
                    "value": "数据不出园区",
                    "source_id": "SRC-SALES",
                    "quote": "销售补充：数据不能离开园区",
                    "speaker_role": "sales",
                },
            ],
            "conflicts": [
                {
                    "field": "goals",
                    "conflicting_values": ["三个月", "六个月"],
                    "source_ids": ["SRC-MEETING", "SRC-SALES"],
                    "clarification_question": "试点周期到底是三个月还是六个月？",
                }
            ],
        }


def test_extract_experience_validates_and_normalizes_model_output() -> None:
    experience = asyncio.run(
        extract_experience_from_text(
            "一份项目复盘原文",
            "SRC-EXP-001",
            StubJsonModelClient(),
        )
    )

    assert experience.source_id == "SRC-EXP-001"
    assert experience.embedding_text is not None
    assert experience.embedding_text.startswith("经验名称：")
    assert "适用问题：" in experience.embedding_text
    assert "标签：视觉质检" in experience.embedding_text
    assert "模型乱写" not in experience.embedding_text


def test_extract_experience_rejects_empty_source_text() -> None:
    with pytest.raises(ValueError, match="raw_text must not be empty"):
        asyncio.run(extract_experience_from_text(" ", "SRC-EXP-001", StubJsonModelClient()))


def test_extract_experience_rejects_overlong_source_with_split_instruction() -> None:
    with pytest.raises(ValueError, match="split the source"):
        asyncio.run(
            extract_experience_from_text(
                "长" * (MAX_RAW_TEXT_CHARACTERS + 1),
                "SRC-EXP-001",
                StubJsonModelClient(),
            )
        )


def test_extract_profile_forces_pending_confirmation_and_source_ids() -> None:
    profile = asyncio.run(
        extract_profile_from_text(
            "一份客户会议纪要",
            ["SRC-CUST-001"],
            StubProfileModelClient(),
        )
    )

    assert profile.profile_status == "pending_confirmation"
    assert profile.source_ids == ["SRC-CUST-001"]
    assert "不允许 AI 自动停线" in profile.constraints


def test_extract_profile_rejects_empty_source_ids() -> None:
    with pytest.raises(ValueError, match="source_ids must not be empty"):
        asyncio.run(extract_profile_from_text("一份客户会议纪要", [], StubProfileModelClient()))


def test_extract_profile_keeps_speaker_sources_and_turns_conflict_into_question() -> None:
    profile = asyncio.run(
        extract_profile_from_text(
            "客户原话和销售补充",
            ["SRC-MEETING", "SRC-SALES"],
            MultiSourceProfileClient(),
        )
    )

    assert profile.profile_status == "pending_confirmation"
    assert profile.fact_sources[0].speaker_role == "customer"
    assert profile.fact_sources[1].speaker_role == "sales"
    assert "试点周期到底是三个月还是六个月？" in profile.information_gaps


def test_extract_capabilities_normalizes_output_and_keeps_gray_capability() -> None:
    capabilities = asyncio.run(
        extract_capabilities_from_text(
            "一份包含正式能力和灰度能力的 PRD",
            "SRC-PRD-001",
            StubCapabilityModelClient(),
        )
    )

    assert len(capabilities) == 2
    assert all(item.source_id == "SRC-PRD-001" for item in capabilities)
    assert all("模型乱写" not in item.embedding_text for item in capabilities)
    assert capabilities[0].embedding_text.startswith("能力名称：工业图像只读接入")
    assert capabilities[1].name == "图像可用性预提示"
    assert "灰度能力" in capabilities[1].tags
    assert "不能作为正式质量判断" in capabilities[1].limitations


def test_extract_capabilities_rejects_empty_result() -> None:
    client = StaticJsonModelClient({"capabilities": []})

    with pytest.raises(ValueError, match="at least one capability"):
        asyncio.run(extract_capabilities_from_text("一份 PRD", "SRC-PRD-001", client))


def test_extract_capabilities_drops_plain_unsupported_items() -> None:
    supported = {
        "name": "配置化视觉缺陷识别",
        "description": "对标准图像执行推理与缺陷定位。",
        "input": "标准图像和模型版本。",
        "output": "缺陷类别和位置框。",
        "prerequisites": "分类规则已经确认。",
        "limitations": "模型分值不是放行结论，不直接停止设备。",
        "dependencies": [],
        "tags": ["视觉质检"],
        "source_id": "SRC-PRD-001",
        "embedding_text": None,
    }
    false_capability = {
        "name": "自动停线",
        "description": "当前产品不支持的审核反例。",
        "input": "异常结果。",
        "output": "停线指令。",
        "prerequisites": None,
        "limitations": "V2.4 不支持自动停线。",
        "dependencies": [],
        "tags": ["审核反例", "能力越界"],
        "source_id": "SRC-PRD-001",
        "embedding_text": None,
    }
    client = StaticJsonModelClient({"capabilities": [supported, false_capability]})

    capabilities = asyncio.run(
        extract_capabilities_from_text(
            "产品支持视觉识别，但自动停线不在 V2.4 支持范围。",
            "SRC-PRD-001",
            client,
        )
    )

    assert [item.name for item in capabilities] == ["配置化视觉缺陷识别"]


def test_extract_capabilities_keeps_explicit_audit_rejection() -> None:
    rejected = {
        "name": "供应商采购订单自动创建",
        "description": "当前产品不支持的审核反例。",
        "input": "补货建议。",
        "output": "采购订单。",
        "prerequisites": None,
        "limitations": "当前版本不支持创建采购订单。",
        "dependencies": [],
        "tags": ["审核反例", "能力越界"],
        "source_id": "SRC-PRD-002",
        "embedding_text": None,
    }
    client = StaticJsonModelClient({"capabilities": [rejected]})

    capabilities = asyncio.run(
        extract_capabilities_from_text(
            "任何无人自动下单候选都应在能力审核时打回。",
            "SRC-PRD-002",
            client,
        )
    )

    assert [item.name for item in capabilities] == ["供应商采购订单自动创建"]


def test_extract_capabilities_marks_duplicate_names_for_human_merge() -> None:
    item = {
        "name": "重复能力",
        "description": "能力说明。",
        "input": "输入。",
        "output": "输出。",
        "prerequisites": None,
        "limitations": "能力限制。",
        "dependencies": [],
        "tags": [],
        "source_id": "SRC-PRD-001",
        "embedding_text": None,
    }
    client = StaticJsonModelClient({"capabilities": [item, item.copy()]})

    capabilities = asyncio.run(extract_capabilities_from_text("一份 PRD", "SRC-PRD-001", client))

    assert len(capabilities) == 2
    assert capabilities[1].merge_suggestion is not None
    assert "人工合并" in capabilities[1].merge_suggestion


@pytest.mark.parametrize(
    "invalid_change",
    [
        {"unexpected": "不允许的额外字段"},
    ],
)
def test_extract_capabilities_rejects_invalid_card_fields(invalid_change: dict) -> None:
    item = {
        "name": "能力名称",
        "description": "能力说明。",
        "input": "输入。",
        "output": "输出。",
        "prerequisites": None,
        "limitations": "能力限制。",
        "dependencies": [],
        "tags": [],
        "source_id": "SRC-PRD-001",
        "embedding_text": None,
        **invalid_change,
    }
    client = StaticJsonModelClient({"capabilities": [item]})

    with pytest.raises(ValidationError):
        asyncio.run(extract_capabilities_from_text("一份 PRD", "SRC-PRD-001", client))


@pytest.mark.parametrize(
    ("invalid_change", "warning_label"),
    [
        ({"limitations": None}, "限制"),
        ({"input": None}, "输入"),
        ({"output": None}, "输出"),
    ],
)
def test_extract_capabilities_keeps_missing_fields_pending_review(
    invalid_change: dict,
    warning_label: str,
) -> None:
    item = {
        "name": "能力名称",
        "description": "能力说明。",
        "input": "输入。",
        "output": "输出。",
        "prerequisites": None,
        "limitations": "能力限制。",
        "dependencies": [],
        "tags": [],
        "source_id": "SRC-PRD-001",
        "embedding_text": None,
        **invalid_change,
    }
    client = StaticJsonModelClient({"capabilities": [item]})

    capabilities = asyncio.run(extract_capabilities_from_text("一份 PRD", "SRC-PRD-001", client))

    assert len(capabilities) == 1
    assert any(warning_label in warning for warning in capabilities[0].review_warnings)
