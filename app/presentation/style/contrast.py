import colorsys


def rgb(color: str) -> tuple[int, int, int]:
    if len(color) != 7 or not color.startswith("#"):
        raise ValueError("color must use #RRGGBB")
    try:
        return tuple(int(color[offset : offset + 2], 16) for offset in (1, 3, 5))
    except ValueError as exc:
        raise ValueError("color must use #RRGGBB") from exc


def relative_luminance(color: str) -> float:
    channels = []
    for value in rgb(color):
        normalized = value / 255
        channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def ensure_contrast(
    foreground: str,
    background: str,
    *,
    minimum_ratio: float = 4.5,
) -> str:
    """Adjust only HSL lightness and choose the smallest passing change."""
    if not 1 <= minimum_ratio <= 21:
        raise ValueError("minimum contrast ratio must be between 1 and 21")
    if contrast_ratio(foreground, background) >= minimum_ratio:
        return foreground.upper()
    red, green, blue = (value / 255 for value in rgb(foreground))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    candidates: list[tuple[float, str]] = []
    for step in range(1001):
        candidate_lightness = step / 1000
        candidate_rgb = colorsys.hls_to_rgb(hue, candidate_lightness, saturation)
        candidate = "#" + "".join(f"{round(channel * 255):02X}" for channel in candidate_rgb)
        if contrast_ratio(candidate, background) >= minimum_ratio:
            candidates.append((abs(candidate_lightness - lightness), candidate))
    if not candidates:
        raise ValueError("requested contrast ratio cannot be reached")
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def ensure_contrast_on_surfaces(
    foreground: str,
    backgrounds: tuple[str, ...],
    *,
    minimum_ratio: float = 4.5,
) -> str:
    result = foreground
    for _ in range(3):
        failing = [
            background
            for background in backgrounds
            if contrast_ratio(result, background) < minimum_ratio
        ]
        if not failing:
            return result
        result = ensure_contrast(result, failing[0], minimum_ratio=minimum_ratio)
    if any(contrast_ratio(result, background) < minimum_ratio for background in backgrounds):
        black_score = min(contrast_ratio("#000000", item) for item in backgrounds)
        white_score = min(contrast_ratio("#FFFFFF", item) for item in backgrounds)
        result = "#000000" if black_score >= white_score else "#FFFFFF"
    if any(contrast_ratio(result, background) < minimum_ratio for background in backgrounds):
        raise ValueError("one foreground cannot satisfy contrast on all requested surfaces")
    return result
