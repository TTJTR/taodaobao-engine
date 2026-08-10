import uuid

import pytest
from pydantic import ValidationError

from app.presentation.layouts.diagnostics import diagnose_layout
from app.presentation.layouts.engine import LayoutEngine
from app.presentation.layouts.registry import default_layout_registry
from app.schemas.presentation import PresentationSpecData


def _component(component_type: str, index: int) -> dict:
    if component_type == "title":
        return {
            "component_id": uuid.uuid4(),
            "component_type": "title",
            "text": f"页面标题 {index}",
        }
    return {
        "component_id": uuid.uuid4(),
        "component_type": component_type,
        "heading": f"证据 {index}",
        "body": f"经过验证的事实 {index}",
        "fact_binding": {
            "claim_id": uuid.uuid4(),
            "claim_key": f"claim:{index}",
            "evidence_ids": [uuid.uuid4()],
            "source_ids": [uuid.uuid4()],
            "content_mode": "verbatim",
        },
    }


@pytest.mark.parametrize(
    ("token", "business_count"),
    [
        ("cover", 0),
        ("title_body", 1),
        ("two_column", 2),
        ("three_cards", 3),
        ("evidence_grid", 4),
    ],
)
def test_phase_a_templates_produce_non_overlapping_bounded_geometry(
    token: str, business_count: int
) -> None:
    components = [_component("title", 0)]
    components.extend(_component("evidence_card", index) for index in range(business_count))
    spec = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": token,
                    "components": components,
                }
            ],
        }
    )

    positioned = LayoutEngine().position(spec)
    report = diagnose_layout(positioned)

    assert report.passed is True
    assert len(positioned.slides[0].components) == len(components)
    assert token in default_layout_registry.tokens


def test_unknown_layout_token_is_rejected_before_layout() -> None:
    with pytest.raises(ValidationError):
        PresentationSpecData.model_validate(
            {
                "schema_version": "slide-schema-v1",
                "presentation_id": uuid.uuid4(),
                "slides": [
                    {
                        "slide_id": uuid.uuid4(),
                        "layout_token": "freeform_from_ai",
                        "components": [_component("title", 0)],
                    }
                ],
            }
        )


def test_layout_rejects_content_over_slot_capacity() -> None:
    title = _component("title", 0)
    title["text"] = "超" * 121
    spec = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "title_body",
                    "components": [title],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="character capacity"):
        LayoutEngine().position(spec)
