import uuid

import pytest
from pydantic import ValidationError

from app.schemas.presentation import (
    ComparisonComponent,
    EvidenceCardComponent,
    MetricComponent,
    ProcessComponent,
    SlideSchema,
    SourceListComponent,
    TimelineComponent,
)


def _fact_binding() -> dict:
    return {
        "claim_id": uuid.uuid4(),
        "claim_key": "capability.erp.delivery",
        "evidence_ids": [uuid.uuid4()],
        "source_ids": [uuid.uuid4()],
        "content_mode": "verbatim",
    }


def test_slide_schema_accepts_discriminated_semantic_components() -> None:
    slide = SlideSchema.model_validate(
        {
            "slide_id": uuid.uuid4(),
            "layout_token": "title_body",
            "components": [
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "title",
                    "text": "客户数字化转型方案",
                },
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "evidence_card",
                    "heading": "已验证能力",
                    "body": "具备 ERP 交付能力",
                    "fact_binding": _fact_binding(),
                },
            ],
        }
    )

    assert slide.components[0].component_type == "title"
    assert isinstance(slide.components[1], EvidenceCardComponent)
    assert slide.components[1].fact_binding.content_mode == "verbatim"


@pytest.mark.parametrize(
    "component",
    [
        {
            "component_id": uuid.uuid4(),
            "component_type": "evidence_card",
            "heading": "缺少证据绑定",
            "body": "不得通过校验",
        },
        {
            "component_id": uuid.uuid4(),
            "component_type": "evidence_card",
            "heading": "包含未知字段",
            "body": "不得通过校验",
            "fact_binding": _fact_binding(),
            "invented_fact": "not allowed",
        },
    ],
)
def test_business_component_rejects_missing_binding_or_extra_fields(component: dict) -> None:
    with pytest.raises(ValidationError):
        SlideSchema.model_validate(
            {
                "slide_id": uuid.uuid4(),
                "layout_token": "title_body",
                "components": [component],
            }
        )


def test_phase_b_components_parse_with_independent_fact_bindings() -> None:
    def bound_item(label: str) -> dict:
        return {
            "item_id": uuid.uuid4(),
            "label": label,
            "text": "经过验证的事实原文",
            "fact_binding": _fact_binding(),
        }

    source_binding = _fact_binding()
    source_binding["content_mode"] = "label_only"
    source_id = source_binding["source_ids"][0]
    components = [
        {
            "component_id": uuid.uuid4(),
            "component_type": "metric",
            "label": "年度营收",
            "value": "2025年营业收入为1438亿元",
            "fact_binding": _fact_binding(),
        },
        {
            "component_id": uuid.uuid4(),
            "component_type": "comparison",
            "heading": "方案对比",
            "left": bound_item("现状"),
            "right": bound_item("目标"),
        },
        {
            "component_id": uuid.uuid4(),
            "component_type": "timeline",
            "heading": "实施节奏",
            "items": [bound_item("第一阶段"), bound_item("第二阶段")],
        },
        {
            "component_id": uuid.uuid4(),
            "component_type": "process",
            "heading": "交付流程",
            "steps": [bound_item("输入"), bound_item("输出")],
        },
        {
            "component_id": uuid.uuid4(),
            "component_type": "source_list",
            "heading": "事实来源",
            "sources": [
                {
                    "item_id": uuid.uuid4(),
                    "label": "经授权的飞书资料",
                    "source_id": source_id,
                    "fact_binding": source_binding,
                }
            ],
        },
    ]

    parsed_types = []
    for token, component in zip(
        ("metric_highlight", "comparison", "timeline", "process", "source_list"),
        components,
        strict=True,
    ):
        slide = SlideSchema.model_validate(
            {
                "slide_id": uuid.uuid4(),
                "layout_token": token,
                "components": [component],
            }
        )
        parsed_types.append(type(slide.components[0]))

    assert parsed_types == [
        MetricComponent,
        ComparisonComponent,
        TimelineComponent,
        ProcessComponent,
        SourceListComponent,
    ]
