from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path

from defusedxml.ElementTree import fromstring
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integrations.presentation_planner import MockSlidePlanner  # noqa: E402
from app.presentation.layouts.engine import LayoutEngine  # noqa: E402
from app.presentation.layouts.paginator import SlidePaginator  # noqa: E402
from app.presentation.layouts.registry import default_layout_registry  # noqa: E402
from app.presentation.render_ir import (  # noqa: E402
    RenderIRBuilder,
    SystemLabelCatalog,
)
from app.presentation.style.profile_builder import StyleProfileBuilder  # noqa: E402
from app.presentation.style.template_compiler import (  # noqa: E402
    TemplateCompiler,
    compiled_bundle_hash,
)
from app.schemas.presentation import (  # noqa: E402
    FactAtom,
    FactLedger,
    LedgerEvidence,
    PlanningFact,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
)
from app.services.evidence_guard import EvidenceGuard  # noqa: E402
from app.services.html_renderer import HTMLRenderer  # noqa: E402
from app.services.html_renderer_v2 import HTMLRendererV2, HTMLShadowAuditor  # noqa: E402
from app.services.pptx_parser import SafePPTXParser  # noqa: E402
from app.services.slide_plan_materializer import SlidePlanMaterializer  # noqa: E402

SLIDE_PART = re.compile(r"^ppt/slides/slide\d+\.xml$")


def _browser_path() -> str | None:
    candidates = (
        os.environ.get("PRESENTATION_TEST_BROWSER"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _source_text(path: Path) -> set[str]:
    values = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not SLIDE_PART.fullmatch(name):
                continue
            root = fromstring(archive.read(name))
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] == "t" and element.text:
                    text = element.text.strip()
                    if len(text) >= 8:
                        values.add(text)
    return values


def _ledger(sample: str) -> FactLedger:
    values = (
        ("financial:revenue", "2025年营业收入为1438亿元", "verified_fact"),
        ("process:evidence", "AI只负责页面规划，事实内容由证据账本提供", "method"),
        ("capability:delivery", "已审核能力按客户需求组合并保留来源追踪", "capability"),
    )
    facts = []
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"html-v2:{sample}:run")
    for index, (claim_key, text, boundary) in enumerate(values):
        claim_id = uuid.uuid5(run_id, f"claim:{index}")
        source_id = uuid.uuid5(run_id, f"source:{index}")
        facts.append(
            FactAtom(
                claim_id=claim_id,
                claim_key=claim_key,
                verbatim_text=text,
                boundary=boundary,
                allowed_labels=(claim_key,),
                evidence=(
                    LedgerEvidence(
                        evidence_id=uuid.uuid5(run_id, f"evidence:{index}"),
                        source_id=source_id,
                        source_version=1,
                        quote=text,
                    ),
                ),
            )
        )
    return FactLedger(run_id=run_id, facts=tuple(facts))


async def _plan(presentation_id: uuid.UUID, ledger: FactLedger, preferred: tuple[str, ...]):
    context = SlidePlanningContext(
        presentation_id=presentation_id,
        audience="enterprise-review",
        language="zh-CN",
        mode="strict",
    )
    catalog = tuple(
        PlanningFact(
            claim_id=fact.claim_id,
            claim_key=fact.claim_key,
            boundary=fact.boundary,
            verbatim_text=fact.verbatim_text,
            source_count=len({item.source_id for item in fact.evidence}),
        )
        for fact in ledger.facts
    )
    constraints = SlidePlanningStyleConstraints(
        allowed_layout_tokens=default_layout_registry.tokens,
        preferred_layout_tokens=preferred,
        max_pages=8,
        max_components_per_page=4,
    )
    return await MockSlidePlanner().plan_slides(context, catalog, constraints)


def _build_sample(source: Path, output: Path) -> tuple[dict, str]:
    parser = SafePPTXParser()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    features = parser.extract_features(source, mode="sanitized_visual")
    legacy_style = parser.parse(source, mode="sanitized_visual")
    profile = StyleProfileBuilder().build(
        features,
        profile_id=uuid.uuid5(uuid.NAMESPACE_URL, f"html-v2-style:{digest}"),
    )
    bundle = TemplateCompiler().compile(profile)
    templates = tuple(
        item.layout_template for item in bundle.candidates if item.status == "needs_review"
    )
    registry = default_layout_registry.with_templates(templates)
    preferred = tuple(dict.fromkeys(item.layout_template.token for item in bundle.candidates))
    presentation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"html-v2-presentation:{digest}")
    ledger = _ledger(source.stem)
    plan = asyncio.run(_plan(presentation_id, ledger, preferred))
    spec = SlidePaginator().paginate(SlidePlanMaterializer().materialize(plan, ledger))
    provenance = SystemLabelCatalog().build_provenance(spec, ledger)
    guard = EvidenceGuard()
    preflight, snapshot = guard.validate_bindings(spec, ledger)
    if not preflight.passed:
        raise ValueError("FactBinding preflight failed")
    positioned = LayoutEngine(registry).position(spec)
    positioned_report = guard.validate_positioned_spec(positioned, ledger, snapshot)
    if not positioned_report.passed:
        raise ValueError("PositionedSpec evidence validation failed")
    render_ir = RenderIRBuilder().build(
        positioned,
        profile,
        compiled_style_hash=compiled_bundle_hash(bundle),
        text_provenance=provenance,
    )
    render_report = guard.validate_render_ir(render_ir, ledger)
    if not render_report.passed:
        raise ValueError("RenderIR evidence validation failed")
    html = HTMLRendererV2().render(render_ir)
    legacy_html = HTMLRenderer().render(positioned, legacy_style)
    audit = HTMLShadowAuditor().audit(legacy_html, html, render_ir)
    if not audit.passed:
        raise ValueError("HTML shadow audit failed: " + ",".join(audit.errors))
    source_text = _source_text(source)
    leaks = sorted(text for text in source_text if text in html)
    html_path = output / f"{source.stem}.html"
    html_path.write_text(html, encoding="utf-8")
    colors = profile.design_tokens.colors
    text_layouts = [
        node.layout
        for slide in render_ir.slides
        for node in slide.layers
        if node.node_type == "text"
    ]
    return (
        {
            "source": str(source.resolve()),
            "source_sha256": digest,
            "html": str(html_path.resolve()),
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "slides": len(render_ir.slides),
            "nodes": sum(len(slide.layers) for slide in render_ir.slides),
            "text_nodes": audit.text_node_count,
            "font_measurement": {
                "opentype": sum(
                    item.measurement_method == "opentype" for item in text_layouts
                ),
                "conservative": sum(
                    item.measurement_method == "conservative" for item in text_layouts
                ),
                "font_hashes": sorted(
                    {item.font_file_hash for item in text_layouts if item.font_file_hash}
                ),
            },
            "palette": colors.model_dump(mode="json"),
            "archetypes": [item.archetype_token for item in profile.layout_archetypes],
            "compiled_template_hash": compiled_bundle_hash(bundle),
            "source_text_fragments_audited": len(source_text),
            "source_text_leaks": leaks,
            "evidence_checked": render_report.checked_components,
            "passed": not leaks,
        },
        html,
    )


def _screenshots(html_by_sample: dict[str, str], output: Path) -> dict:
    executable = _browser_path()
    if executable is None:
        return {"available": False, "reason": "browser executable not found"}
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        for sample, html in html_by_sample.items():
            sample_results = []
            for viewport in ((1440, 900), (390, 844)):
                page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
                page.set_content(html, wait_until="load")
                slide_count = page.locator(".slide").count()
                first_slide = page.locator("svg.slide").first
                slide_box = first_slide.bounding_box()
                svg_box = slide_box
                view_box = first_slide.get_attribute("viewBox")
                visible_nodes = page.locator("svg [data-node-id]").evaluate_all(
                    "nodes => nodes.filter(node => { const b=node.getBoundingClientRect(); "
                    "return b.width > 0 && b.height > 0; }).length"
                )
                screenshot = output / f"{sample}-{viewport[0]}x{viewport[1]}.png"
                page.locator(".slide").first.screenshot(path=str(screenshot))
                canvas_contained = (
                    slide_box is not None
                    and svg_box is not None
                    and 0 < slide_box["width"] <= viewport[0] + 1
                    and 0 < slide_box["height"] <= viewport[1] + 1
                    and abs(slide_box["width"] / slide_box["height"] - 16 / 9) < 0.01
                    and svg_box["width"] > 0
                    and svg_box["height"] > 0
                    and svg_box["x"] >= slide_box["x"] - 1
                    and svg_box["y"] >= slide_box["y"] - 1
                    and svg_box["x"] + svg_box["width"]
                    <= slide_box["x"] + slide_box["width"] + 1
                    and svg_box["y"] + svg_box["height"]
                    <= slide_box["y"] + slide_box["height"] + 1
                    and view_box == "0 0 1600 900"
                )
                sample_results.append(
                    {
                        "viewport": list(viewport),
                        "slide_count": slide_count,
                        "visible_nodes": visible_nodes,
                        "slide_box": slide_box,
                        "svg_box": svg_box,
                        "view_box": view_box,
                        "canvas_contained": canvas_contained,
                        "screenshot": str(screenshot.resolve()),
                        "size_bytes": screenshot.stat().st_size,
                        "passed": slide_count > 0
                        and visible_nodes > 0
                        and canvas_contained
                        and screenshot.stat().st_size > 0,
                    }
                )
                page.close()
            results[sample] = sample_results
        browser.close()
    return {"available": True, "samples": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "html-v2-validation")
    args = parser.parse_args()
    samples = args.samples or [ROOT / f"test{index}.pptx" for index in range(1, 4)]
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    html_by_sample = {}
    for sample in samples:
        report, html = _build_sample(sample.resolve(), args.output)
        reports.append(report)
        html_by_sample[sample.stem] = html
    screenshots = _screenshots(html_by_sample, args.output)
    palette_signatures = {
        json.dumps(item["palette"], sort_keys=True, ensure_ascii=False) for item in reports
    }
    report = {
        "schema_version": "html-v2-validation-v1",
        "samples": reports,
        "screenshots": screenshots,
        "distinct_palette_count": len(palette_signatures),
        "passed": all(item["passed"] for item in reports)
        and len(palette_signatures) >= 2
        and (
            not screenshots.get("available")
            or all(
                capture["passed"]
                for captures in screenshots["samples"].values()
                for capture in captures
            )
        ),
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"html-v2-validation: passed={report['passed']} samples={len(reports)} "
        f"palettes={len(palette_signatures)} report={report_path}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
