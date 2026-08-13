import hashlib
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from test_render_ir_builder import _inputs

from app.presentation.render_ir import RenderIRBuilder
from app.services.html_renderer_v2 import HTMLRendererV2, HTMLShadowAuditor


def _rendered_pair():
    positioned, style, _, provenance, _ = _inputs()
    render_ir = RenderIRBuilder().build(
        positioned,
        style,
        compiled_style_hash="c" * 64,
        text_provenance=provenance,
    )
    expected_text = "".join(
        run.text
        for slide in render_ir.slides
        for node in slide.layers
        if node.node_type == "text"
        for run in node.runs
    )
    return render_ir, HTMLRendererV2().render(render_ir), expected_text


def _browser_path() -> str | None:
    candidates = (
        os.environ.get("PRESENTATION_TEST_BROWSER"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def test_renderer_v2_emits_fixed_svg_and_exact_text_hashes_without_script() -> None:
    render_ir, html, expected_text = _rendered_pair()

    report = HTMLShadowAuditor().audit(expected_text, html, render_ir)

    assert report.passed is True
    assert report.slide_count == len(render_ir.slides)
    assert report.text_node_count == 2
    assert 'viewBox="0 0 1600 900"' in html
    assert "<script" not in html.lower()
    assert "https://" not in html.lower()
    for slide in render_ir.slides:
        for node in slide.layers:
            if node.node_type == "text":
                digest = hashlib.sha256("".join(run.text for run in node.runs).encode()).hexdigest()
                assert f'data-content-hash="{digest}"' in html
                assert (
                    f'data-font-measurement="{node.layout.measurement_method}"' in html
                )


def test_renderer_v2_html_escapes_text_that_looks_like_script() -> None:
    render_ir, _, _ = _rendered_pair()
    text_node = next(
        node
        for node in render_ir.slides[0].layers
        if node.node_type == "text" and node.runs[0].content_origin == "system_label"
    )
    malicious = "<script>alert(1)</script>"
    run = text_node.runs[0].model_copy(update={"text": malicious})
    line = text_node.layout.line_boxes[0].model_copy(
        update={"text": malicious, "end_offset": len(malicious)}
    )
    layout = text_node.layout.model_copy(update={"line_boxes": (line,), "line_breaks": ()})
    changed_node = text_node.model_copy(update={"runs": (run,), "layout": layout})
    slide = render_ir.slides[0]
    changed_layers = tuple(
        changed_node if node.node_id == text_node.node_id else node for node in slide.layers
    )
    changed_ir = render_ir.model_copy(
        update={"slides": (slide.model_copy(update={"layers": changed_layers}),)}
    )

    html = HTMLRendererV2().render(changed_ir)

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_shadow_auditor_rejects_removed_text_node() -> None:
    render_ir, html, expected_text = _rendered_pair()
    first_node = next(
        node
        for slide in render_ir.slides
        for node in slide.layers
        if node.node_type == "text"
    )
    tampered = html.replace(f'data-node-id="{first_node.node_id}"', "data-node-id=\"removed\"")

    report = HTMLShadowAuditor().audit(expected_text, tampered, render_ir)

    assert report.passed is False
    assert "SHADOW_TEXT_HASHES_MISMATCH" in report.errors


def test_shadow_auditor_rejects_removed_font_fingerprint() -> None:
    render_ir, html, expected_text = _rendered_pair()
    tampered = html.replace('data-font-measurement="opentype"', 'data-font-measurement="lost"')
    if tampered == html:
        tampered = html.replace(
            'data-font-measurement="conservative"', 'data-font-measurement="lost"', 1
        )

    report = HTMLShadowAuditor().audit(expected_text, tampered, render_ir)

    assert report.passed is False
    assert "SHADOW_FONT_METADATA_MISMATCH" in report.errors


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844)])
def test_renderer_v2_canvas_is_nonblank_and_contained(
    viewport: tuple[int, int], tmp_path: Path
) -> None:
    executable = _browser_path()
    if executable is None:
        pytest.skip("set PRESENTATION_TEST_BROWSER to run browser visual validation")
    _, html, _ = _rendered_pair()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.set_content(html, wait_until="load")
        slide = page.locator(".slide").first
        box = slide.bounding_box()
        assert box is not None
        assert box["width"] <= viewport[0] + 1
        assert box["height"] <= viewport[1] + 1
        assert abs(box["width"] / box["height"] - 16 / 9) < 0.01
        assert page.locator("svg text").count() == 2
        visible_nodes = page.locator("svg [data-node-id]").evaluate_all(
            "nodes => nodes.filter(node => { const box = node.getBoundingClientRect(); "
            "return box.width > 0 && box.height > 0; }).length"
        )
        assert visible_nodes >= 3
        screenshot = tmp_path / f"render-ir-{viewport[0]}x{viewport[1]}.png"
        slide.screenshot(path=str(screenshot))
        assert screenshot.stat().st_size > 0
        browser.close()
