import uuid

from app.presentation.style.features import (
    CanvasFeature,
    ColorSample,
    FontSample,
    StyleFeatureSet,
)
from app.presentation.style.profile_builder import StyleProfileBuilder
from app.schemas.style_profile import VisualStyleProfileV2


def adapt_legacy_style_profile(
    visual_json: dict,
    *,
    profile_id: uuid.UUID,
) -> VisualStyleProfileV2:
    """Convert the current v1 provider payload without changing its persisted shape."""
    palette = visual_json.get("palette") or {}
    typography = visual_json.get("typography") or {}
    primary = _color(palette.get("primary"), "#2563EB")
    secondary = _color(palette.get("secondary"), primary)
    accent = _color(palette.get("accent"), secondary)
    background = _color(palette.get("background"), "#FFFFFF")
    foreground = _color(palette.get("foreground"), "#111827")
    heading_font = str(
        typography.get("heading_font") or typography.get("title") or "Aptos Display"
    )
    body_font = str(typography.get("body_font") or typography.get("body") or "Aptos")
    base_size_px = int(typography.get("base_size_px") or 18)
    heading_size = min(48, max(26, base_size_px * float(typography.get("scale_ratio") or 1.7)))
    body_size = min(24, max(16, base_size_px * 0.75))
    features = StyleFeatureSet(
        canvas=CanvasFeature(
            width_emu=12_192_000,
            height_emu=6_858_000,
            aspect_ratio=16 / 9,
        ),
        color_samples=(
            ColorSample(color=primary, role="theme", frequency=3, page_coverage=1),
            ColorSample(color=secondary, role="theme", frequency=2, page_coverage=1),
            ColorSample(color=accent, role="theme", frequency=1, page_coverage=1),
            ColorSample(color=background, role="background", frequency=3, page_coverage=1),
            ColorSample(color=foreground, role="text", frequency=3, page_coverage=1),
        ),
        font_samples=(
            FontSample(
                family=heading_font,
                size_pt=heading_size,
                weight="bold",
                role="title",
                frequency=1,
            ),
            FontSample(
                family=body_font,
                size_pt=body_size,
                role="body",
                frequency=1,
            ),
        ),
        shape_samples=(),
        page_samples=(),
        master_layouts=(),
        source_mode="theme_only",
    )
    return StyleProfileBuilder().build(features, profile_id=profile_id)


def _color(value: object, fallback: str) -> str:
    normalized = str(value or fallback).upper()
    if len(normalized) == 7 and normalized.startswith("#"):
        try:
            int(normalized[1:], 16)
        except ValueError:
            return fallback
        return normalized
    return fallback
