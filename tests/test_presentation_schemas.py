import uuid

import pytest
from pydantic import ValidationError

from app.schemas.presentation import EvidenceCardComponent, SlideSchema


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
            "layout_token": "title-and-evidence",
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
                "layout_token": "evidence-only",
                "components": [component],
            }
        )
