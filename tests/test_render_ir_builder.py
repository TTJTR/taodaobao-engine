import uuid

import pytest

from app.presentation.layouts.engine import LayoutEngine
from app.presentation.render_ir import (
    RenderIRBuilder,
    SystemLabelCatalog,
    SystemLabelTrace,
    TextProvenance,
)
from app.presentation.style.legacy_adapter import adapt_legacy_style_profile
from app.schemas.presentation import (
    FactAtom,
    FactLedger,
    LedgerEvidence,
    PresentationSpecData,
)
from app.services.evidence_guard import EvidenceGuard


def _inputs():
    claim_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    source_id = uuid.uuid4()
    title_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    ledger = FactLedger(
        run_id=uuid.uuid4(),
        facts=(
            FactAtom(
                claim_id=claim_id,
                claim_key="financial:revenue",
                verbatim_text="2025年营业收入1438亿元",
                boundary="verified_fact",
                evidence=(
                    LedgerEvidence(
                        evidence_id=evidence_id,
                        source_id=source_id,
                        source_version=1,
                        quote="2025年营业收入1438亿元",
                    ),
                ),
            ),
        ),
    )
    spec = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": presentation_id,
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "title_body",
                    "components": [
                        {
                            "component_id": title_id,
                            "component_type": "title",
                            "text": "方案核心结论",
                        },
                        {
                            "component_id": fact_id,
                            "component_type": "key_message",
                            "text": ledger.facts[0].verbatim_text,
                            "fact_binding": {
                                "claim_id": claim_id,
                                "claim_key": "financial:revenue",
                                "evidence_ids": [evidence_id],
                                "source_ids": [source_id],
                                "content_mode": "verbatim",
                            },
                        },
                    ],
                }
            ],
        }
    )
    style = adapt_legacy_style_profile(
        {
            "palette": {"primary": "#173F8A", "accent": "#FF8A3D"},
            "typography": {"title": "Arial", "body": "Arial"},
        },
        profile_id=uuid.uuid4(),
    )
    provenance = {
        f"{title_id}:text": TextProvenance(
            content_origin="system_label",
            system_label_trace=SystemLabelTrace(
                token="slide.executive_summary.title",
                catalog_version="v1",
            ),
        )
    }
    return LayoutEngine().position(spec), style, ledger, provenance, fact_id


def test_builder_produces_guarded_render_ir_without_changing_fact_text() -> None:
    positioned, style, ledger, _, _ = _inputs()
    semantic_spec = PresentationSpecData(
        schema_version="slide-schema-v1",
        presentation_id=positioned.presentation_id,
        slides=[
            {
                "slide_id": slide.slide_id,
                "layout_token": slide.layout_token,
                "components": [item.component for item in slide.components],
            }
            for slide in positioned.slides
        ],
    )
    provenance = SystemLabelCatalog().build_provenance(semantic_spec, ledger)

    render_ir = RenderIRBuilder().build(
        positioned,
        style,
        compiled_style_hash="c" * 64,
        text_provenance=provenance,
    )

    report = EvidenceGuard().validate_render_ir(render_ir, ledger)
    texts = [
        run.text
        for slide in render_ir.slides
        for node in slide.layers
        if node.node_type == "text"
        for run in node.runs
    ]
    assert report.passed is True
    assert ledger.facts[0].verbatim_text in texts
    assert render_ir.positioned_spec_hash != render_ir.compiled_style_hash


def test_builder_emits_canvas_background_and_title_accent_without_title_card() -> None:
    positioned, style, _, provenance, _ = _inputs()

    render_ir = RenderIRBuilder().build(
        positioned,
        style,
        compiled_style_hash="c" * 64,
        text_provenance=provenance,
    )

    layers = render_ir.slides[0].layers
    background = layers[0]
    title_component_id = positioned.slides[0].components[0].component.component_id
    title_shapes = [
        node
        for node in layers
        if node.node_type == "shape" and node.node_id != background.node_id
        and node.node_id == uuid.uuid5(title_component_id, "title-accent")
    ]
    assert background.node_type == "shape"
    assert background.geometry.model_dump() == {
        "x": 0,
        "y": 0,
        "width": 1600,
        "height": 900,
    }
    assert len(title_shapes) == 1
    assert title_shapes[0].geometry.height == 6
    assert all(
        node.node_id != uuid.uuid5(title_component_id, "shape")
        for node in layers
        if node.node_type == "shape"
    )


def test_builder_keeps_component_text_inside_padded_shape() -> None:
    positioned, style, _, provenance, fact_id = _inputs()

    render_ir = RenderIRBuilder().build(
        positioned,
        style,
        compiled_style_hash="c" * 64,
        text_provenance=provenance,
    )

    layers = render_ir.slides[0].layers
    shape = next(node for node in layers if node.node_id == uuid.uuid5(fact_id, "shape"))
    text = next(
        node
        for node in layers
        if node.node_type == "text" and node.component_id == fact_id
    )
    assert text.geometry.x > shape.geometry.x
    assert text.geometry.y > shape.geometry.y
    assert text.geometry.x + text.geometry.width < shape.geometry.x + shape.geometry.width
    assert text.geometry.y + text.geometry.height < shape.geometry.y + shape.geometry.height
    assert shape.geometry.height <= 260


def test_system_label_catalog_rejects_ai_modified_title() -> None:
    positioned, _, ledger, _, _ = _inputs()
    title = positioned.slides[0].components[0].component.model_copy(
        update={"text": "AI 自由生成的夸张标题"}
    )
    spec = PresentationSpecData(
        schema_version="slide-schema-v1",
        presentation_id=positioned.presentation_id,
        slides=[
            {
                "slide_id": positioned.slides[0].slide_id,
                "layout_token": positioned.slides[0].layout_token,
                "components": [
                    title,
                    positioned.slides[0].components[1].component,
                ],
            }
        ],
    )

    with pytest.raises(ValueError, match="outside the system label catalog"):
        SystemLabelCatalog().build_provenance(spec, ledger)


def test_builder_fails_closed_when_non_fact_text_has_no_provenance() -> None:
    positioned, style, _, _, _ = _inputs()

    with pytest.raises(ValueError, match="missing text provenance"):
        RenderIRBuilder().build(
            positioned,
            style,
            compiled_style_hash="c" * 64,
            text_provenance={},
        )


def test_builder_rejects_provenance_override_for_fact_bound_text() -> None:
    positioned, style, _, provenance, fact_id = _inputs()
    provenance[f"{fact_id}:text"] = TextProvenance(
        content_origin="system_label",
        system_label_trace=SystemLabelTrace(token="unsafe.override", catalog_version="v1"),
    )

    with pytest.raises(ValueError, match="must not override provenance"):
        RenderIRBuilder().build(
            positioned,
            style,
            compiled_style_hash="c" * 64,
            text_provenance=provenance,
        )


def test_builder_rejects_text_that_still_overflows_at_minimum_font() -> None:
    positioned, style, _, provenance, _ = _inputs()
    title = positioned.slides[0].components[0]
    oversized = title.component.model_copy(update={"text": "超长标题" * 2_000})
    changed_title = title.model_copy(update={"component": oversized})
    changed_slide = positioned.slides[0].model_copy(
        update={"components": (changed_title, *positioned.slides[0].components[1:])}
    )
    changed_spec = positioned.model_copy(update={"slides": (changed_slide,)})

    with pytest.raises(ValueError, match="text layout overflowed"):
        RenderIRBuilder().build(
            changed_spec,
            style,
            compiled_style_hash="c" * 64,
            text_provenance=provenance,
        )
