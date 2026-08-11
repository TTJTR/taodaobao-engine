import uuid

from app.presentation.fidelity import assess_cross_format_fidelity, expected_texts
from app.presentation.layouts.engine import LayoutEngine
from app.schemas.presentation import PresentationSpecData


def _spec():
    claim_id = uuid.uuid4()
    semantic = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "title_body",
                    "components": [
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "title",
                            "text": "客户方案核心结论",
                        },
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "key_message",
                            "text": "2025年营业收入为1438亿元",
                            "fact_binding": {
                                "claim_id": claim_id,
                                "claim_key": "revenue:2025",
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
    return LayoutEngine().position(semantic)


def test_cross_format_accepts_visual_approximation_but_requires_exact_facts() -> None:
    spec = _spec()
    texts = expected_texts(spec)

    report = assess_cross_format_fidelity(
        spec,
        html_texts=texts,
        pptx_texts=texts,
        html_slide_count=1,
        pptx_slide_count=1,
        maximum_geometry_delta_ratio=0.06,
    )

    assert report.passed is True
    assert report.fact_text_exact is True
    assert report.geometry_assessment == "visual_approximation"
    assert "不承诺像素级一致" in report.disclaimer


def test_cross_format_rejects_a_changed_financial_number() -> None:
    spec = _spec()
    texts = expected_texts(spec)
    tampered = tuple(text.replace("1438", "1538") for text in texts)

    report = assess_cross_format_fidelity(
        spec,
        html_texts=texts,
        pptx_texts=tampered,
        html_slide_count=1,
        pptx_slide_count=1,
        maximum_geometry_delta_ratio=0.02,
    )

    assert report.passed is False
    assert report.fact_text_exact is False
    assert "CROSS_FORMAT_FACT_TEXT_MISMATCH" in report.errors
