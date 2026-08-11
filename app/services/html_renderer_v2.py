import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.presentation.render_ir import RenderIR, TextNode

SAFE_FONT_PATTERN = re.compile(r"^[\w\s,.'\-]+$", re.UNICODE)


class HTMLRendererV2:
    """Render frozen RenderIR without layout, fact, database, network, or AI access."""

    version = "svg-render-ir-v1"

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or (
            Path(__file__).parents[1] / "templates" / "presentation_v2"
        )
        self.environment = Environment(
            loader=FileSystemLoader(directory),
            autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
            undefined=StrictUndefined,
        )

    def render(self, render_ir: RenderIR) -> str:
        return self.environment.get_template("base.html").render(
            render_ir=render_ir,
            text_hash=self._text_hash,
            origins=self._origins,
            claim_ids=self._claim_ids,
            safe_font=self._safe_font,
            weight=lambda value: {"regular": 400, "semibold": 600, "bold": 700}[value],
        )

    @staticmethod
    def _text_hash(node: TextNode) -> str:
        return hashlib.sha256("".join(run.text for run in node.runs).encode()).hexdigest()

    @staticmethod
    def _origins(node: TextNode) -> str:
        return ",".join(run.content_origin for run in node.runs)

    @staticmethod
    def _claim_ids(node: TextNode) -> str:
        return ",".join(
            str(run.fact_trace.claim_id) for run in node.runs if run.fact_trace is not None
        )

    @staticmethod
    def _safe_font(value: str) -> str:
        if SAFE_FONT_PATTERN.fullmatch(value) is None:
            raise ValueError("RenderIR contains an unsafe font name")
        return value


@dataclass(frozen=True)
class HTMLShadowAuditReport:
    passed: bool
    errors: tuple[str, ...]
    slide_count: int
    text_node_count: int


class HTMLShadowAuditor:
    def audit(
        self, legacy_html: str, shadow_html: str, render_ir: RenderIR
    ) -> HTMLShadowAuditReport:
        collector = _HTMLAuditCollector()
        collector.feed(shadow_html)
        errors = list(collector.errors)
        expected_slides = {str(slide.slide_id) for slide in render_ir.slides}
        expected_nodes = {
            str(node.node_id): HTMLRendererV2._text_hash(node)
            for slide in render_ir.slides
            for node in slide.layers
            if node.node_type == "text"
        }
        expected_fonts = {
            str(node.node_id): (
                node.layout.measurement_method,
                node.layout.font_file_hash or "",
            )
            for slide in render_ir.slides
            for node in slide.layers
            if node.node_type == "text"
        }
        if set(collector.slide_ids) != expected_slides:
            errors.append("SHADOW_SLIDE_IDS_MISMATCH")
        if collector.text_hashes != expected_nodes:
            errors.append("SHADOW_TEXT_HASHES_MISMATCH")
        if collector.font_metadata != expected_fonts:
            errors.append("SHADOW_FONT_METADATA_MISMATCH")
        if collector.view_boxes != {(0, 0, render_ir.canvas.width, render_ir.canvas.height)}:
            errors.append("SHADOW_CANVAS_MISMATCH")

        legacy_text = _visible_text(legacy_html)
        for slide in render_ir.slides:
            for node in slide.layers:
                if node.node_type == "text":
                    for run in node.runs:
                        if run.text not in legacy_text:
                            errors.append(f"LEGACY_TEXT_MISSING:{node.node_id}")
        return HTMLShadowAuditReport(
            passed=not errors,
            errors=tuple(errors),
            slide_count=len(collector.slide_ids),
            text_node_count=len(collector.text_hashes),
        )


class _HTMLAuditCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []
        self.slide_ids: list[str] = []
        self.text_hashes: dict[str, str] = {}
        self.font_metadata: dict[str, tuple[str, str]] = {}
        self.view_boxes: set[tuple[int, int, int, int]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script":
            self.errors.append("SCRIPT_TAG_FORBIDDEN")
        for name in ("href", "src", "xlink:href"):
            value = values.get(name) or ""
            if value.startswith(("http://", "https://", "//", "javascript:")):
                self.errors.append("EXTERNAL_RESOURCE_FORBIDDEN")
        if tag == "section" and values.get("data-slide-id"):
            self.slide_ids.append(values["data-slide-id"])
        if tag == "text" and values.get("data-node-id"):
            self.text_hashes[values["data-node-id"]] = values.get("data-content-hash", "")
            self.font_metadata[values["data-node-id"]] = (
                values.get("data-font-measurement", ""),
                values.get("data-font-hash", ""),
            )
        if tag == "svg" and values.get("viewbox"):
            try:
                self.view_boxes.add(tuple(int(item) for item in values["viewbox"].split()))
            except ValueError:
                self.errors.append("INVALID_VIEWBOX")


class _VisibleTextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"style", "script", "head"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script", "head"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _visible_text(html: str) -> str:
    collector = _VisibleTextCollector()
    collector.feed(html)
    return "".join(collector.parts)
