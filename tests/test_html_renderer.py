import uuid

import pytest

from app.schemas.presentation import PresentationSpecData, VisualStyleProfileData
from app.services.html_renderer import HTMLRenderer


def _style(*, heading_font: str = "Microsoft YaHei") -> VisualStyleProfileData:
    return VisualStyleProfileData.model_validate(
        {
            "palette": {
                "primary": "#123456",
                "secondary": "#345678",
                "accent": "#E11D48",
                "background": "#FFFFFF",
                "foreground": "#111827",
            },
            "typography": {
                "heading_font": heading_font,
                "body_font": "Inter",
                "base_size_px": 18,
                "scale_ratio": 1.5,
            },
            "spacing_grid": {
                "base_unit_px": 8,
                "slide_padding_units": 8,
                "component_gap_units": 3,
            },
            "layout_grammar": ["title-and-evidence"],
        }
    )


def _spec(text: str) -> PresentationSpecData:
    claim_id = uuid.uuid4()
    return PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": 'layout"><img src=x onerror=alert(1)>',
                    "components": [
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "title",
                            "text": "可信企业方案",
                        },
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "key_message",
                            "text": text,
                            "fact_binding": {
                                "claim_id": claim_id,
                                "claim_key": "verified:fact",
                                "evidence_ids": [uuid.uuid4()],
                                "source_ids": [uuid.uuid4()],
                                "content_mode": "verbatim",
                            },
                        },
                    ],
                }
            ],
        }
    )


def test_renderer_maps_css_variables_and_escapes_all_business_text() -> None:
    html = HTMLRenderer().render(_spec('<script>alert("财务数据")</script>'), _style())

    assert "--primary-color: #123456" in html
    assert '--title-font: "Microsoft YaHei"' in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert" in html
    assert "onerror=alert(1)&gt;" in html
    assert "<img src=x" not in html
    assert "script-src 'none'" in html


def test_renderer_rejects_css_injection_in_font_name() -> None:
    with pytest.raises(ValueError, match="unsafe CSS"):
        HTMLRenderer().render(_spec("安全事实"), _style(heading_font='Arial";color:red'))
