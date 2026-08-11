import pytest

from app.presentation.style.contrast import (
    contrast_ratio,
    ensure_contrast,
    ensure_contrast_on_surfaces,
)
from app.presentation.style.features import CanvasFeature, ColorSample, StyleFeatureSet
from app.presentation.style.token_normalizer import build_design_tokens


def _features(text_color: str) -> StyleFeatureSet:
    return StyleFeatureSet(
        canvas=CanvasFeature(width_emu=12_192_000, height_emu=6_858_000, aspect_ratio=16 / 9),
        color_samples=(
            ColorSample(color="#FFFFFF", role="background", frequency=5, page_coverage=1),
            ColorSample(color="#4F76E8", role="theme", frequency=5, page_coverage=0.5),
            ColorSample(color=text_color, role="text", frequency=5, page_coverage=0.8),
        ),
        font_samples=(),
        shape_samples=(),
        page_samples=(),
        master_layouts=(),
        source_mode="theme_only",
    )


def test_low_contrast_color_is_adjusted_by_lightness_to_wcag_aa() -> None:
    original = "#B8C2D1"

    adjusted = ensure_contrast(original, "#FFFFFF")

    assert adjusted != original
    assert contrast_ratio(adjusted, "#FFFFFF") >= 4.5


def test_design_tokens_keep_primary_and_muted_text_readable_on_canvas_and_surface() -> None:
    tokens = build_design_tokens(_features("#CDD3DC")).colors

    for text in (tokens.text_primary, tokens.text_muted):
        assert contrast_ratio(text, tokens.canvas) >= 4.5
        assert contrast_ratio(text, tokens.surface) >= 4.5


def test_incompatible_light_and_dark_surfaces_fail_instead_of_claiming_readability() -> None:
    with pytest.raises(ValueError, match="cannot satisfy contrast"):
        ensure_contrast_on_surfaces(
            "#777777", ("#000000", "#FFFFFF"), minimum_ratio=7
        )
