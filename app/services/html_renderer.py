import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from app.schemas.presentation import PositionedPresentationSpec, VisualStyleProfileData

SAFE_FONT_PATTERN = re.compile(r"^[\w\s,.'\-]+$", re.UNICODE)


class HTMLRenderer:
    """Render validated schemas without database, network, AI, or script execution."""

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or Path(__file__).parents[1] / "templates" / "presentation"
        self.environment = Environment(
            loader=FileSystemLoader(directory),
            autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
            undefined=StrictUndefined,
        )

    LAYOUT_TEMPLATES = {
        "cover": "layouts/cover.html",
        "title_body": "layouts/title_body.html",
        "two_column": "layouts/two_column.html",
        "three_cards": "layouts/three_cards.html",
        "evidence_grid": "layouts/evidence_grid.html",
        "metric_highlight": "layouts/metric_highlight.html",
        "comparison": "layouts/comparison.html",
        "timeline": "layouts/timeline.html",
        "process": "layouts/process.html",
        "source_list": "layouts/source_list.html",
    }

    def render(self, spec: PositionedPresentationSpec, style: VisualStyleProfileData) -> str:
        template = self.environment.get_template("base.html")
        rendered_slides = []
        for slide in spec.slides:
            template_name = self.LAYOUT_TEMPLATES.get(slide.layout_token)
            if template_name is None:
                raise ValueError(f"unsupported layout template: {slide.layout_token}")
            rendered_slides.append(
                Markup(self.environment.get_template(template_name).render(slide=slide))
            )
        return template.render(
            presentation_id=spec.presentation_id,
            rendered_slides=rendered_slides,
            css=self._css_variables(style),
        )

    @staticmethod
    def _css_variables(style: VisualStyleProfileData) -> dict[str, str | int | float]:
        title_font = HTMLRenderer._safe_font(style.typography.heading_font)
        body_font = HTMLRenderer._safe_font(style.typography.body_font)
        return {
            "primary": style.palette.primary,
            "secondary": style.palette.secondary,
            "accent": style.palette.accent,
            "background": style.palette.background,
            "foreground": style.palette.foreground,
            "title_font": Markup(f'"{title_font}"'),
            "body_font": Markup(f'"{body_font}"'),
            "body_size": style.typography.base_size_px,
            "title_scale": style.typography.scale_ratio,
            "space_unit": style.spacing_grid.base_unit_px,
        }

    @staticmethod
    def _safe_font(value: str) -> str:
        if not SAFE_FONT_PATTERN.fullmatch(value):
            raise ValueError("font name contains unsafe CSS characters")
        return value
