import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.presentation.render_ir import (
    CanvasSpec,
    FactTrace,
    RenderIR,
    RenderSlide,
    TextLayoutEngine,
    TextLayoutRequest,
    TextNode,
    TextRun,
)
from app.presentation.render_ir.font_metrics import FontFaceMetrics, OpenTypeFontMetricsProvider
from app.schemas.presentation import ComponentGeometry, FactAtom, FactLedger, LedgerEvidence
from app.services.evidence_guard import EvidenceGuard


def _fact_trace(mode: str = "verbatim") -> FactTrace:
    return FactTrace(
        claim_id=uuid.uuid4(),
        claim_key="financial:revenue",
        evidence_ids=(uuid.uuid4(),),
        source_ids=(uuid.uuid4(),),
        content_mode=mode,
    )


def test_text_run_requires_exact_trace_for_content_origin() -> None:
    with pytest.raises(ValidationError, match="exactly one matching trace"):
        TextRun(
            run_id=uuid.uuid4(),
            text="1438亿元",
            content_origin="ledger_verbatim",
            system_label_trace={"token": "metric.revenue", "catalog_version": "v1"},
        )


def test_text_layout_preserves_cjk_and_numbers_exactly() -> None:
    text = "2025年营业收入1438亿元，数据不得修改"
    layout = TextLayoutEngine().layout(
        TextLayoutRequest(
            text=text,
            requested_font="Microsoft YaHei",
            font_size=28,
            min_font_size=18,
            box_width=180,
            box_height=240,
        )
    )

    assert "".join(line.text for line in layout.line_boxes) == text
    assert layout.font_size >= 18
    assert layout.line_breaks == tuple(line.end_offset for line in layout.line_boxes[:-1])
    assert all("1438" not in line.text or "1438亿元" in line.text for line in layout.line_boxes)


def test_text_layout_marks_unbreakable_number_as_overflow_instead_of_splitting() -> None:
    number = "12345678901234567890亿元"

    layout = TextLayoutEngine().layout(
        TextLayoutRequest(
            text=number,
            requested_font="Microsoft YaHei",
            font_size=24,
            min_font_size=12,
            box_width=30,
            box_height=100,
        )
    )

    assert [line.text for line in layout.line_boxes] == [number]
    assert layout.overflowed is True


class _FixedFontProvider:
    def __init__(self, face: FontFaceMetrics | None) -> None:
        self.face = face

    def resolve(self, families: tuple[str, ...], text: str) -> FontFaceMetrics | None:
        return self.face


def test_text_layout_uses_opentype_advances_and_freezes_font_hash() -> None:
    face = FontFaceMetrics(
        family="Fixture Sans",
        file_path=Path("fixture.ttf"),
        file_hash="f" * 64,
        units_per_em=1_000,
        advances={ord("A"): 600, ord("B"): 400},
    )

    layout = TextLayoutEngine(_FixedFontProvider(face)).layout(
        TextLayoutRequest(
            text="AB",
            requested_font="Requested Font",
            font_size=20,
            box_width=100,
            box_height=100,
        )
    )

    assert layout.engine_version == "text-layout-v2"
    assert layout.measurement_method == "opentype"
    assert layout.resolved_font == "Fixture Sans"
    assert layout.font_file_hash == "f" * 64
    assert layout.line_boxes[0].width == 20


def test_text_layout_marks_conservative_fallback_without_fake_font_hash() -> None:
    layout = TextLayoutEngine(_FixedFontProvider(None)).layout(
        TextLayoutRequest(
            text="AB",
            requested_font="Unavailable Font",
            font_size=20,
            box_width=100,
            box_height=100,
        )
    )

    assert layout.measurement_method == "conservative"
    assert layout.resolved_font == "Unavailable Font"
    assert layout.font_file_hash is None


def test_font_provider_does_not_treat_untrusted_family_as_a_path(tmp_path: Path) -> None:
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    outside = tmp_path / "outside.ttf"
    outside.write_bytes(b"not-a-font")
    provider = OpenTypeFontMetricsProvider(search_roots=(fonts,))

    assert provider._find_font("../outside") is None
    assert provider._find_font("..\\outside") is None


def test_text_node_rejects_layout_that_changes_fact_text() -> None:
    run = TextRun(
        run_id=uuid.uuid4(),
        text="1438亿元",
        content_origin="ledger_verbatim",
        fact_trace=_fact_trace(),
    )
    layout = TextLayoutEngine().layout(
        TextLayoutRequest(
            text="1538亿元",
            requested_font="Arial",
            font_size=24,
            box_width=300,
            box_height=100,
        )
    )

    with pytest.raises(ValidationError, match="preserve TextRun content exactly"):
        TextNode(
            node_id=uuid.uuid4(),
            component_id=uuid.uuid4(),
            geometry=ComponentGeometry(x=0, y=0, width=300, height=100),
            z_index=1,
            fill="#111827",
            font_weight="regular",
            runs=(run,),
            layout=layout,
        )


def test_evidence_guard_rejects_render_ir_number_mutation() -> None:
    claim_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    source_id = uuid.uuid4()
    ledger = FactLedger(
        run_id=uuid.uuid4(),
        facts=(
            FactAtom(
                claim_id=claim_id,
                claim_key="financial:revenue",
                verbatim_text="1438亿元",
                boundary="verified_fact",
                evidence=(
                    LedgerEvidence(
                        evidence_id=evidence_id,
                        source_id=source_id,
                        source_version=1,
                        quote="1438亿元",
                    ),
                ),
            ),
        ),
    )
    run = TextRun(
        run_id=uuid.uuid4(),
        text="1538亿元",
        content_origin="ledger_verbatim",
        fact_trace=FactTrace(
            claim_id=claim_id,
            claim_key="financial:revenue",
            evidence_ids=(evidence_id,),
            source_ids=(source_id,),
            content_mode="verbatim",
        ),
    )
    layout = TextLayoutEngine().layout(
        TextLayoutRequest(
            text=run.text,
            requested_font="Arial",
            font_size=24,
            box_width=300,
            box_height=100,
        )
    )
    node = TextNode(
        node_id=uuid.uuid4(),
        component_id=uuid.uuid4(),
        geometry=ComponentGeometry(x=0, y=0, width=300, height=100),
        z_index=1,
        fill="#111827",
        font_weight="regular",
        runs=(run,),
        layout=layout,
    )
    render_ir = RenderIR(
        presentation_id=uuid.uuid4(),
        canvas=CanvasSpec(),
        positioned_spec_hash="a" * 64,
        compiled_style_hash="b" * 64,
        slides=(RenderSlide(slide_id=uuid.uuid4(), layers=(node,)),),
    )

    report = EvidenceGuard().validate_render_ir(render_ir, ledger)

    assert report.passed is False
    assert [failure.code for failure in report.failures] == ["VERBATIM_CONTENT_MISMATCH"]
