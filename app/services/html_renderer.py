import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from app.schemas.presentation import PresentationSpecData, VisualStyleProfileData

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

    def render(self, spec: PresentationSpecData, style: VisualStyleProfileData) -> str:
        template = self.environment.get_template("base.html")
        return template.render(spec=spec, css=self._css_variables(style))

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
