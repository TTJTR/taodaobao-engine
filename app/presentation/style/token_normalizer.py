from collections import Counter

from app.presentation.style.contrast import contrast_ratio, ensure_contrast_on_surfaces
from app.presentation.style.features import StyleFeatureSet
from app.schemas.style_profile import (
    ColorTokens,
    DecorationTokens,
    DesignTokens,
    ShapeTokens,
    SpacingTokens,
    TypographyToken,
    TypographyTokens,
)

SIZE_RANGES = {
    "display": (32.0, 48.0),
    "title": (26.0, 38.0),
    "subtitle": (20.0, 28.0),
    "body": (16.0, 24.0),
    "evidence": (14.0, 20.0),
    "footnote": (10.0, 14.0),
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return round(min(upper, max(lower, value)), 2)


def _blend(first: str, second: str, ratio: float) -> str:
    channels = []
    for offset in (1, 3, 5):
        left = int(first[offset : offset + 2], 16)
        right = int(second[offset : offset + 2], 16)
        channels.append(round(left * ratio + right * (1 - ratio)))
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def _ranked_colors(features: StyleFeatureSet) -> list[str]:
    weights: Counter[str] = Counter()
    for sample in features.color_samples:
        role_weight = 3 if sample.role in {"theme", "fill"} else 1
        weights[sample.color] += sample.frequency * role_weight
    return [color for color, _ in weights.most_common()]


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[offset : offset + 2], 16) for offset in (1, 3, 5))


def _is_chromatic(color: str) -> bool:
    red, green, blue = _rgb(color)
    return max(red, green, blue) - min(red, green, blue) >= 28


def _family(features: StyleFeatureSet, roles: set[str], fallback: str) -> str:
    weights: Counter[str] = Counter()
    for sample in features.font_samples:
        if sample.role in roles:
            weights[sample.family] += sample.frequency
    return weights.most_common(1)[0][0] if weights else fallback


def _size(features: StyleFeatureSet, role: str, fallback: float) -> float:
    values = [
        sample.size_pt
        for sample in features.font_samples
        for _ in range(min(sample.frequency, 20))
        if sample.role == role
    ]
    raw = sorted(values)[len(values) // 2] if values else fallback
    return _clamp(raw, *SIZE_RANGES[role])


def build_design_tokens(features: StyleFeatureSet) -> DesignTokens:
    colors = _ranked_colors(features)
    canvas = next(
        (sample.color for sample in features.color_samples if sample.role == "background"),
        "#FFFFFF",
    )
    non_canvas = [color for color in colors if color != canvas]
    brand_colors = [color for color in non_canvas if _is_chromatic(color)]
    primary = brand_colors[0] if brand_colors else "#2563EB"
    secondary = brand_colors[1] if len(brand_colors) > 1 else _blend(primary, canvas, 0.7)
    accent = brand_colors[2] if len(brand_colors) > 2 else secondary
    text_candidates = [
        sample.color
        for sample in features.color_samples
        if sample.role == "text" and contrast_ratio(sample.color, canvas) >= 4.5
    ]
    surface = _blend(canvas, primary, 0.94)
    text_seed = text_candidates[0] if text_candidates else "#111827"
    text_primary = ensure_contrast_on_surfaces(text_seed, (canvas, surface))
    text_muted = ensure_contrast_on_surfaces(_blend(text_primary, canvas, 0.62), (canvas, surface))
    heading_family = _family(features, {"display", "title", "subtitle"}, "Aptos Display")
    body_family = _family(features, {"body", "evidence", "footnote"}, "Aptos")
    line_widths = [
        sample.line_width_emu
        for sample in features.shape_samples
        if sample.line_width_emu is not None
    ]
    border_width = (
        min(80, round(sorted(line_widths)[len(line_widths) // 2] / 12_700))
        if line_widths
        else 0
    )
    filled_ratio = (
        sum(sample.fill is not None for sample in features.shape_samples)
        / max(1, len(features.shape_samples))
    )
    return DesignTokens(
        colors=ColorTokens(
            canvas=canvas,
            surface=surface,
            primary=primary,
            secondary=secondary,
            accent=accent,
            text_primary=text_primary,
            text_muted=text_muted,
            border=_blend(primary, canvas, 0.35),
        ),
        typography=TypographyTokens(
            display=TypographyToken(
                font_family=heading_family,
                size_pt=_size(features, "display", 40),
                weight="bold",
                line_spacing=1.1,
                color_token="text_primary",
            ),
            title=TypographyToken(
                font_family=heading_family,
                size_pt=_size(features, "title", 32),
                weight="bold",
                line_spacing=1.15,
                color_token="text_primary",
            ),
            subtitle=TypographyToken(
                font_family=heading_family,
                size_pt=_size(features, "subtitle", 24),
                weight="semibold",
                line_spacing=1.2,
                color_token="text_primary",
            ),
            body=TypographyToken(
                font_family=body_family,
                size_pt=_size(features, "body", 18),
                weight="regular",
                line_spacing=1.35,
                color_token="text_primary",
            ),
            evidence=TypographyToken(
                font_family=body_family,
                size_pt=_size(features, "evidence", 16),
                weight="regular",
                line_spacing=1.35,
                color_token="text_primary",
            ),
            footnote=TypographyToken(
                font_family=body_family,
                size_pt=_size(features, "footnote", 11),
                weight="regular",
                line_spacing=1.25,
                color_token="text_muted",
            ),
        ),
        spacing=SpacingTokens(
            page_margin=650,
            section_gap=350,
            component_gap=300,
            card_padding=300,
        ),
        shape=ShapeTokens(
            radius=120 if filled_ratio >= 0.25 else 40,
            border_width=border_width,
            border_style="solid" if border_width else "none",
            shadow="soft" if filled_ratio >= 0.5 else "none",
            surface_fill="surface",
        ),
        decoration=DecorationTokens(
            title_rule=border_width > 0,
            accent_bar=filled_ratio >= 0.2,
            page_number=True,
            header_footer=False,
        ),
    )
