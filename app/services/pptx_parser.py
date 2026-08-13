from __future__ import annotations

import posixpath
import re
import zipfile
from collections import Counter
from colorsys import hls_to_rgb, rgb_to_hls
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from xml.etree import ElementTree

from app.presentation.style.features import (
    CanvasFeature,
    ColorSample,
    FontSample,
    MasterLayoutFeature,
    MasterPlaceholder,
    NormalizedGeometry,
    PageSample,
    ShapeSample,
    StyleFeatureSet,
)
from app.schemas.presentation import (
    Palette,
    SpacingGrid,
    Typography,
    VisualStyleProfileData,
)
from app.services.pptx_sanitized_visual import (
    SanitizedSlide,
    SanitizedVisualParseError,
    parse_sanitized_slide,
)

# Relationship traversal and theme parsing are ported from Presentation AI.
# Master/layout inheritance and geometry are ported from pptx-renderer.
# See THIRD_PARTY_NOTICES.md for pinned revisions and licenses.

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
THEME_COLOR_SLOTS = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)
DEFAULT_CLR_MAP = {
    "bg1": "lt1",
    "tx1": "dk1",
    "bg2": "lt2",
    "tx2": "dk2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}
OFFICE_FONT_MAP = {
    "Calibri": "Inter",
    "Calibri Light": "Inter",
    "Cambria": "Merriweather",
    "Arial": "Inter",
    "Times New Roman": "Lora",
    "Verdana": "Open Sans",
    "Georgia": "Source Serif Pro",
    "Trebuchet MS": "Montserrat",
    "Tahoma": "Inter",
    "Century Gothic": "Poppins",
    "Garamond": "Cormorant Garamond",
    "Book Antiqua": "Libre Baskerville",
    "Palatino": "Libre Baskerville",
    "Franklin Gothic Medium": "Manrope",
    "Impact": "Sora",
    "Lucida Sans": "Nunito",
}
SLIDE_PART_PATTERN = re.compile(r"^ppt/slides/slide\d+\.xml$")
NEUTRAL_COLORS = {"#000000", "#FFFFFF", "#F8FAFC", "#F9FAFB"}


class PPTXParseError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedTheme:
    theme_xml: bytes
    slide_master_xml: bytes
    slide_layout_xml: tuple[bytes, ...]


@dataclass(frozen=True)
class ParsedTheme:
    palette: dict[str, str | None]
    heading_font: str
    body_font: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_first(root: ElementTree.Element | None, local_name: str) -> ElementTree.Element | None:
    if root is None:
        return None
    return next(
        (element for element in root.iter() if _local_name(element.tag) == local_name), None
    )


def _find_child(
    parent: ElementTree.Element | None, local_name: str
) -> ElementTree.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if _local_name(child.tag) == local_name), None)


def _parse_xml(content: bytes) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise PPTXParseError("PPTX contains malformed theme metadata") from exc


def _safe_part_path(base_dir: str, target: str) -> str:
    if not target or target.startswith(("/", "\\")) or ":" in target:
        raise PPTXParseError("PPTX relationship contains an invalid target")
    normalized = posixpath.normpath(posixpath.join(base_dir, target.replace("\\", "/")))
    path = PurePosixPath(normalized)
    if normalized.startswith("../") or path.is_absolute() or ".." in path.parts:
        raise PPTXParseError("PPTX relationship escapes the archive root")
    return path.as_posix()


def _read_part(archive: zipfile.ZipFile, part_name: str, max_bytes: int) -> bytes:
    try:
        info = archive.getinfo(part_name)
    except KeyError as exc:
        raise PPTXParseError(f"PPTX is missing required part: {part_name}") from exc
    if info.file_size > max_bytes:
        raise PPTXParseError(f"PPTX metadata part is too large: {part_name}")
    return archive.read(info)


def _validate_archive(
    archive: zipfile.ZipFile,
    *,
    max_entries: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: int,
) -> None:
    entries = archive.infolist()
    if len(entries) > max_entries:
        raise PPTXParseError("PPTX archive contains too many parts")
    if sum(entry.file_size for entry in entries) > max_uncompressed_bytes:
        raise PPTXParseError("PPTX archive expands beyond the allowed size")
    for entry in entries:
        ratio = entry.file_size / max(1, entry.compress_size)
        if entry.file_size > 1_000_000 and ratio > max_compression_ratio:
            raise PPTXParseError("PPTX archive contains a suspiciously compressed part")


def _relationship_target(xml: bytes, relationship_suffix: str) -> str | None:
    root = _parse_xml(xml)
    for relationship in root.findall(f"{{{REL_NS}}}Relationship"):
        if relationship.get("Type", "").endswith(relationship_suffix):
            return relationship.get("Target")
    return None


def _relationship_targets(xml: bytes, relationship_suffix: str) -> list[str]:
    root = _parse_xml(xml)
    return [
        target
        for relationship in root.findall(f"{{{REL_NS}}}Relationship")
        if relationship.get("Type", "").endswith(relationship_suffix)
        and (target := relationship.get("Target"))
    ]


def resolve_theme_xml(archive: zipfile.ZipFile, max_part_bytes: int) -> ResolvedTheme:
    presentation_rels = _read_part(
        archive, "ppt/_rels/presentation.xml.rels", max_part_bytes
    )
    master_target = _relationship_target(presentation_rels, "/slideMaster")
    if not master_target:
        raise PPTXParseError("PPTX presentation has no slide master relationship")

    master_path = _safe_part_path("ppt", master_target)
    if not master_path.startswith("ppt/slideMasters/"):
        raise PPTXParseError("Slide master relationship points outside ppt/slideMasters")
    master_xml = _read_part(archive, master_path, max_part_bytes)

    master_dir, master_name = posixpath.split(master_path)
    master_rels_path = f"{master_dir}/_rels/{master_name}.rels"
    master_rels = _read_part(archive, master_rels_path, max_part_bytes)
    theme_target = _relationship_target(master_rels, "/theme")
    if not theme_target:
        raise PPTXParseError("PPTX slide master has no theme relationship")

    theme_path = _safe_part_path(master_dir, theme_target)
    if not theme_path.startswith("ppt/theme/"):
        raise PPTXParseError("Theme relationship points outside ppt/theme")
    theme_xml = _read_part(archive, theme_path, max_part_bytes)

    layout_xml: list[bytes] = []
    for layout_target in _relationship_targets(master_rels, "/slideLayout")[:32]:
        layout_path = _safe_part_path(master_dir, layout_target)
        if not layout_path.startswith("ppt/slideLayouts/"):
            raise PPTXParseError("Layout relationship points outside ppt/slideLayouts")
        layout_xml.append(_read_part(archive, layout_path, max_part_bytes))
    return ResolvedTheme(
        theme_xml=theme_xml,
        slide_master_xml=master_xml,
        slide_layout_xml=tuple(layout_xml),
    )


def parse_clr_map(slide_master_xml: bytes) -> dict[str, str]:
    root = _parse_xml(slide_master_xml)
    color_map = dict(DEFAULT_CLR_MAP)
    element = _find_first(root, "clrMap")
    if element is None:
        return color_map
    for logical_name, slot in element.attrib.items():
        if slot in THEME_COLOR_SLOTS:
            color_map[_local_name(logical_name)] = slot
    return color_map


def _apply_color_modifiers(base_hex: str, color_element: ElementTree.Element) -> str:
    red = int(base_hex[1:3], 16) / 255
    green = int(base_hex[3:5], 16) / 255
    blue = int(base_hex[5:7], 16) / 255
    hue, lightness, saturation = rgb_to_hls(red, green, blue)
    for modifier in color_element:
        value = modifier.get("val")
        if value is None:
            continue
        ratio = int(value) / 100_000
        match _local_name(modifier.tag):
            case "lumMod":
                lightness *= ratio
            case "lumOff":
                lightness += ratio
            case "tint":
                lightness += (1 - lightness) * ratio
            case "shade":
                lightness *= ratio
            case "satMod":
                saturation *= ratio
            case "satOff":
                saturation += ratio
    red, green, blue = hls_to_rgb(
        hue, min(1, max(0, lightness)), min(1, max(0, saturation))
    )
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def _color_from_slot(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    srgb = _find_first(element, "srgbClr")
    if srgb is not None and srgb.get("val"):
        base = f"#{srgb.get('val', '').upper()}"
        return _apply_color_modifiers(base, srgb)
    system = _find_first(element, "sysClr")
    if system is not None and system.get("lastClr"):
        base = f"#{system.get('lastClr', '').upper()}"
        return _apply_color_modifiers(base, system)
    return None


def _map_font(font_name: str | None) -> str:
    resolved = font_name or "Inter"
    return OFFICE_FONT_MAP.get(resolved, resolved)


def _font_from_scheme(font_node: ElementTree.Element | None) -> str:
    if font_node is None:
        return "Inter"
    script_fonts = {
        element.get("script"): element.get("typeface")
        for element in font_node
        if _local_name(element.tag) == "font"
    }
    east_asian = _find_child(font_node, "ea")
    latin = _find_child(font_node, "latin")
    typeface = (
        script_fonts.get("Hans")
        or (east_asian.get("typeface") if east_asian is not None else None)
        or (latin.get("typeface") if latin is not None else None)
    )
    return _map_font(typeface)


def parse_theme_palette(theme_xml: bytes) -> ParsedTheme:
    root = _parse_xml(theme_xml)
    color_scheme = _find_first(root, "clrScheme")
    palette = {
        slot: _color_from_slot(_find_child(color_scheme, slot)) for slot in THEME_COLOR_SLOTS
    }

    font_scheme = _find_first(root, "fontScheme")
    major_font = _find_first(font_scheme, "majorFont")
    minor_font = _find_first(font_scheme, "minorFont")
    return ParsedTheme(
        palette=palette,
        heading_font=_font_from_scheme(major_font),
        body_font=_font_from_scheme(minor_font),
    )


def _resolve_color(
    logical_name: str,
    palette: dict[str, str | None],
    color_map: dict[str, str],
    fallback: str,
) -> str:
    return palette.get(color_map.get(logical_name, "")) or fallback


def _text_style_size(root: ElementTree.Element, style_name: str) -> float | None:
    style = _find_first(root, style_name)
    level = _find_child(style, "lvl1pPr")
    default_run = _find_first(level, "defRPr")
    raw_size = default_run.get("sz") if default_run is not None else None
    return int(raw_size) / 100 if raw_size and raw_size.isdigit() else None


def _typography_metrics(slide_master_xml: bytes) -> tuple[int, float]:
    root = _parse_xml(slide_master_xml)
    heading_pt = _text_style_size(root, "titleStyle")
    body_pt = _text_style_size(root, "bodyStyle")
    if not heading_pt or not body_pt:
        return 18, 1.25
    base_px = min(48, max(10, round(body_pt * 4 / 3)))
    scale_ratio = min(2, max(1.01, round(heading_pt / body_pt, 3)))
    return base_px, scale_ratio


def _num_attr(element: ElementTree.Element | None, name: str) -> float:
    if element is None:
        return 0
    try:
        return float(element.get(name, "0"))
    except ValueError:
        return 0


def _shape_xfrm(shape: ElementTree.Element) -> tuple[float, float, float, float] | None:
    properties = _find_child(shape, "spPr")
    transform = _find_child(properties, "xfrm") or _find_child(shape, "xfrm")
    if transform is None:
        return None
    offset = _find_child(transform, "off")
    extent = _find_child(transform, "ext")
    return (
        _num_attr(offset, "x"),
        _num_attr(offset, "y"),
        _num_attr(extent, "cx"),
        _num_attr(extent, "cy"),
    )


def _group_xfrm(group: ElementTree.Element) -> tuple[float, ...] | None:
    properties = _find_child(group, "grpSpPr")
    transform = _find_child(properties, "xfrm")
    if transform is None:
        return None
    offset = _find_child(transform, "off")
    extent = _find_child(transform, "ext")
    child_offset = _find_child(transform, "chOff")
    child_extent = _find_child(transform, "chExt")
    cx, cy = _num_attr(extent, "cx"), _num_attr(extent, "cy")
    return (
        _num_attr(offset, "x"),
        _num_attr(offset, "y"),
        cx,
        cy,
        _num_attr(child_offset, "x"),
        _num_attr(child_offset, "y"),
        _num_attr(child_extent, "cx") or cx or 1,
        _num_attr(child_extent, "cy") or cy or 1,
    )


def _placeholder_type(shape: ElementTree.Element) -> str | None:
    placeholder = _find_first(shape, "ph")
    return placeholder.get("type", "body") if placeholder is not None else None


def _placeholder_tokens(
    tree: ElementTree.Element,
    transform: tuple[float, float, float, float] | None = None,
) -> list[str]:
    tokens: list[str] = []
    for shape in tree:
        if _local_name(shape.tag) == "grpSp":
            group = _group_xfrm(shape)
            if group:
                x, y, cx, cy, child_x, child_y, child_cx, child_cy = group
                sx, sy = cx / child_cx, cy / child_cy
                local = (x - child_x * sx, y - child_y * sy, sx, sy)
                if transform:
                    px, py, psx, psy = transform
                    local = (px + local[0] * psx, py + local[1] * psy, psx * sx, psy * sy)
                tokens.extend(_placeholder_tokens(shape, local))
            else:
                tokens.extend(_placeholder_tokens(shape, transform))
            continue
        placeholder = _placeholder_type(shape)
        if not placeholder:
            continue
        geometry = _shape_xfrm(shape)
        if geometry:
            x, y, width, height = geometry
            if transform:
                px, py, sx, sy = transform
                x, y, width, height = px + x * sx, py + y * sy, width * sx, height * sy
            values = (round(value / 9525) for value in (x, y, width, height))
            tokens.append(f"{placeholder}@{','.join(map(str, values))}")
        else:
            tokens.append(placeholder)
    return tokens


def _layout_grammar(slide_master_xml: bytes, slide_layout_xml: tuple[bytes, ...]) -> list[str]:
    layout_tokens: list[str] = []
    for index, content in enumerate(slide_layout_xml, start=1):
        root = _parse_xml(content)
        common_slide = _find_child(root, "cSld")
        shape_tree = _find_child(common_slide, "spTree")
        name = common_slide.get("name") if common_slide is not None else None
        placeholders = _placeholder_tokens(shape_tree) if shape_tree is not None else []
        summary = ";".join(placeholders[:8]) or "unspecified"
        layout_tokens.append(f"layout:{name or index}|{summary}")
    if layout_tokens:
        return layout_tokens

    root = _parse_xml(slide_master_xml)
    common_slide = _find_child(root, "cSld")
    shape_tree = _find_child(common_slide, "spTree")
    placeholders = _placeholder_tokens(shape_tree) if shape_tree is not None else []
    return [f"master|{';'.join(placeholders[:8]) or 'unspecified'}"]


def _scheme_colors(
    palette: dict[str, str | None], color_map: dict[str, str]
) -> dict[str, str]:
    colors = {slot: color for slot, color in palette.items() if color}
    colors.update(
        {
            logical_name: color
            for logical_name, slot in color_map.items()
            if (color := palette.get(slot))
        }
    )
    return colors


def _visual_pattern(slide: SanitizedSlide, slide_width: float, slide_height: float) -> str:
    if not slide.shapes:
        return "visual-pattern:empty"
    shapes = slide.shapes
    title_band = any(
        shape.y / slide_height < 0.2
        and shape.height / slide_height < 0.3
        and max(shape.font_sizes_pt, default=0) >= 24
        for shape in shapes
    )
    content_boxes = [
        shape
        for shape in shapes
        if 0.15 <= shape.y / slide_height <= 0.9
        and 0.2 <= shape.width / slide_width <= 0.6
    ]
    two_column = any(
        abs(first.y - second.y) / slide_height < 0.15
        and abs(first.x - second.x) / slide_width > 0.25
        for index, first in enumerate(content_boxes)
        for second in content_boxes[index + 1 :]
    )
    full_bleed_image = any(
        shape.kind == "pic"
        and shape.width * shape.height / (slide_width * slide_height) >= 0.7
        for shape in shapes
    )
    graphic_focus = any(shape.kind == "graphicFrame" for shape in shapes)
    repeated_cards = sum(
        1
        for shape in shapes
        if 0.15 <= shape.width / slide_width <= 0.4
        and 0.1 <= shape.height / slide_height <= 0.45
    ) >= 3
    features = [
        name
        for enabled, name in (
            (title_band, "title-band"),
            (two_column, "two-column"),
            (full_bleed_image, "full-bleed-image"),
            (graphic_focus, "graphic-focus"),
            (repeated_cards, "card-grid"),
        )
        if enabled
    ]
    return f"visual-pattern:{'+'.join(features) if features else 'freeform'}"


def _summarize_visual_slides(
    slides: list[SanitizedSlide],
    slide_width: float,
    slide_height: float,
) -> tuple[list[str], str | None, int | None, float | None]:
    patterns = Counter(_visual_pattern(slide, slide_width, slide_height) for slide in slides)
    grammar = [f"{pattern}:count={count}" for pattern, count in patterns.most_common(5)]

    fill_colors = Counter(
        color
        for slide in slides
        for shape in slide.shapes
        for color in shape.fill_colors
        if color not in NEUTRAL_COLORS
    )
    threshold = max(2, round(len(slides) * 0.3))
    primary = next(
        (color for color, count in fill_colors.most_common() if count >= threshold), None
    )

    font_sizes = [
        size
        for slide in slides
        for shape in slide.shapes
        for size in shape.font_sizes_pt
        if 6 <= size <= 72
    ]
    if not font_sizes:
        return grammar, primary, None, None
    size_counts = Counter(font_sizes)
    top_frequency = max(size_counts.values())
    body_pt = min(size for size, count in size_counts.items() if count == top_frequency)
    heading_pt = max(font_sizes)
    base_px = min(48, max(10, round(body_pt * 4 / 3)))
    ratio = min(2, max(1.01, round(heading_pt / body_pt, 3)))
    return grammar, primary, base_px, ratio


def _presentation_size(archive: zipfile.ZipFile, max_part_bytes: int) -> tuple[float, float]:
    root = _parse_xml(_read_part(archive, "ppt/presentation.xml", max_part_bytes))
    size = _find_first(root, "sldSz")
    width = _num_attr(size, "cx")
    height = _num_attr(size, "cy")
    return (width or 12_192_000, height or 6_858_000)


def _normalized_geometry(
    x: float,
    y: float,
    width: float,
    height: float,
    canvas_width: float,
    canvas_height: float,
) -> NormalizedGeometry | None:
    if width <= 0 or height <= 0 or canvas_width <= 0 or canvas_height <= 0:
        return None
    left = max(0, min(10_000, round(x / canvas_width * 10_000)))
    top = max(0, min(10_000, round(y / canvas_height * 10_000)))
    right = max(left + 1, min(10_000, round((x + width) / canvas_width * 10_000)))
    bottom = max(top + 1, min(10_000, round((y + height) / canvas_height * 10_000)))
    if right > 10_000 or bottom > 10_000:
        return None
    return NormalizedGeometry(x=left, y=top, width=right - left, height=bottom - top)


def _font_role(size_pt: float) -> str:
    if size_pt >= 36:
        return "display"
    if size_pt >= 28:
        return "title"
    if size_pt >= 22:
        return "subtitle"
    if size_pt >= 16:
        return "body"
    if size_pt >= 12:
        return "evidence"
    return "footnote"


def _page_sample(
    page_index: int,
    slide: SanitizedSlide,
    canvas_width: float,
    canvas_height: float,
) -> PageSample:
    boxes = tuple(
        geometry
        for shape in slide.shapes
        if (
            geometry := _normalized_geometry(
                shape.x,
                shape.y,
                shape.width,
                shape.height,
                canvas_width,
                canvas_height,
            )
        )
    )
    content = [box for box in boxes if box.y >= 1_500 and box.width >= 1_200]
    centers = sorted(box.x + box.width / 2 for box in content)
    column_count = (
        2
        if any(b - a >= 2_000 for a, b in zip(centers, centers[1:], strict=False))
        else 1
    )
    card_candidates = [
        box for box in content if 1_200 <= box.width <= 4_000 and 800 <= box.height <= 5_000
    ]
    occupied = sum(box.width * box.height for box in boxes)
    return PageSample(
        page_index=page_index,
        boxes=boxes,
        title_band=any(box.y <= 1_500 and box.height <= 2_500 for box in boxes),
        column_count=column_count,
        card_grid=len(card_candidates) >= 3,
        image_region=any(
            shape.kind == "pic"
            and shape.width * shape.height >= canvas_width * canvas_height * 0.25
            for shape in slide.shapes
        ),
        density=min(1, round(occupied / (canvas_width * canvas_height), 4)),
    )


def _master_layout_features(
    layout_xml: tuple[bytes, ...], canvas_width: float, canvas_height: float
) -> tuple[MasterLayoutFeature, ...]:
    layouts = []
    for index, content in enumerate(layout_xml, start=1):
        root = _parse_xml(content)
        common_slide = _find_child(root, "cSld")
        shape_tree = _find_child(common_slide, "spTree")
        placeholders = []
        if shape_tree is not None:
            for shape in shape_tree.iter():
                role = _placeholder_type(shape)
                if role is None:
                    continue
                geometry = _shape_xfrm(shape)
                normalized = (
                    _normalized_geometry(*geometry, canvas_width, canvas_height)
                    if geometry
                    else None
                )
                placeholders.append(MasterPlaceholder(role=role, geometry=normalized))
        layouts.append(
            MasterLayoutFeature(
                name=(common_slide.get("name") if common_slide is not None else None)
                or f"layout-{index}",
                placeholders=tuple(placeholders),
            )
        )
    return tuple(layouts)


class SafePPTXParser:
    """Extract style metadata without opening ordinary slide XML parts."""

    def __init__(
        self,
        *,
        max_part_bytes: int = 2 * 1024 * 1024,
        max_entries: int = 2_000,
        max_uncompressed_bytes: int = 512 * 1024 * 1024,
        max_compression_ratio: int = 200,
        max_visual_slides: int = 50,
    ) -> None:
        self.max_part_bytes = max_part_bytes
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio
        self.max_visual_slides = max_visual_slides

    def extract_features(
        self,
        source: str | Path | BinaryIO,
        *,
        mode: Literal["theme_only", "sanitized_visual"] = "sanitized_visual",
    ) -> StyleFeatureSet:
        """Return sanitized, strongly typed observations for the v2 style compiler."""
        legacy = self.parse(source, mode=mode)
        try:
            with zipfile.ZipFile(source) as archive:
                _validate_archive(
                    archive,
                    max_entries=self.max_entries,
                    max_uncompressed_bytes=self.max_uncompressed_bytes,
                    max_compression_ratio=self.max_compression_ratio,
                )
                resolved = resolve_theme_xml(archive, self.max_part_bytes)
                canvas_width, canvas_height = _presentation_size(archive, self.max_part_bytes)
                color_map = parse_clr_map(resolved.slide_master_xml)
                theme = parse_theme_palette(resolved.theme_xml)
                scheme_colors = _scheme_colors(theme.palette, color_map)
                visual_parts = (
                    sorted(
                        (
                            info.filename
                            for info in archive.infolist()
                            if SLIDE_PART_PATTERN.fullmatch(info.filename)
                        ),
                        key=lambda name: int(re.search(r"\d+", name).group()),
                    )[: self.max_visual_slides]
                    if mode == "sanitized_visual"
                    else []
                )
                slides = [
                    parse_sanitized_slide(
                        _read_part(archive, part_name, self.max_part_bytes), scheme_colors
                    )
                    for part_name in visual_parts
                ]
        except SanitizedVisualParseError as exc:
            raise PPTXParseError("PPTX slide failed sanitized visual parsing") from exc
        except (OSError, zipfile.BadZipFile) as exc:
            raise PPTXParseError("File is not a readable PPTX archive") from exc

        page_count = max(1, len(slides))
        color_counts: Counter[tuple[str, str]] = Counter()
        color_pages: dict[tuple[str, str], set[int]] = {}
        for index, slide in enumerate(slides, start=1):
            for shape in slide.shapes:
                for role, values in (
                    ("fill", shape.fill_colors),
                    ("text", shape.text_colors),
                    ("line", shape.line_colors),
                ):
                    for color in values:
                        key = (color.upper(), role)
                        color_counts[key] += 1
                        color_pages.setdefault(key, set()).add(index)
        for role, color in (
            ("theme", legacy.palette.primary),
            ("theme", legacy.palette.secondary),
            ("theme", legacy.palette.accent),
            ("background", legacy.palette.background),
            ("text", legacy.palette.foreground),
        ):
            color_counts[(color.upper(), role)] += 1
            color_pages.setdefault((color.upper(), role), set()).add(0)

        font_counts: Counter[tuple[str, float, str]] = Counter()
        heading_key = (
            legacy.typography.heading_font,
            legacy.typography.base_size_px * 0.75,
            "title",
        )
        body_key = (
            legacy.typography.body_font,
            legacy.typography.base_size_px * 0.75,
            "body",
        )
        font_counts[heading_key] += 1
        font_counts[body_key] += 1
        for slide in slides:
            for shape in slide.shapes:
                for size in shape.font_sizes_pt:
                    font_counts[(legacy.typography.body_font, size, _font_role(size))] += 1

        shape_samples = []
        for slide in slides:
            for shape in slide.shapes:
                geometry = _normalized_geometry(
                    shape.x,
                    shape.y,
                    shape.width,
                    shape.height,
                    canvas_width,
                    canvas_height,
                )
                if geometry is None:
                    continue
                shape_samples.append(
                    ShapeSample(
                        kind=shape.kind,
                        geometry=geometry,
                        fill=next(iter(shape.fill_colors), None),
                        line=next(iter(shape.line_colors), None),
                        line_width_emu=round(shape.line_widths[0])
                        if shape.line_widths
                        else None,
                    )
                )

        return StyleFeatureSet(
            canvas=CanvasFeature(
                width_emu=round(canvas_width),
                height_emu=round(canvas_height),
                aspect_ratio=round(canvas_width / canvas_height, 6),
            ),
            color_samples=tuple(
                ColorSample(
                    color=color,
                    role=role,
                    frequency=frequency,
                    page_coverage=min(1, len(color_pages[(color, role)]) / page_count),
                )
                for (color, role), frequency in sorted(
                    color_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ),
            font_samples=tuple(
                FontSample(
                    family=family,
                    size_pt=round(size, 2),
                    role=role,
                    frequency=frequency,
                )
                for (family, size, role), frequency in sorted(
                    font_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ),
            shape_samples=tuple(shape_samples[:2_000]),
            page_samples=tuple(
                _page_sample(index, slide, canvas_width, canvas_height)
                for index, slide in enumerate(slides, start=1)
            ),
            master_layouts=_master_layout_features(
                resolved.slide_layout_xml, canvas_width, canvas_height
            ),
            source_mode=mode,
        )

    def parse(
        self,
        source: str | Path | BinaryIO,
        *,
        mode: Literal["theme_only", "sanitized_visual"] = "theme_only",
    ) -> VisualStyleProfileData:
        try:
            with zipfile.ZipFile(source) as archive:
                _validate_archive(
                    archive,
                    max_entries=self.max_entries,
                    max_uncompressed_bytes=self.max_uncompressed_bytes,
                    max_compression_ratio=self.max_compression_ratio,
                )
                resolved = resolve_theme_xml(archive, self.max_part_bytes)
                visual_parts = (
                    sorted(
                        (
                            info.filename
                            for info in archive.infolist()
                            if SLIDE_PART_PATTERN.fullmatch(info.filename)
                        ),
                        key=lambda name: int(re.search(r"\d+", name).group()),
                    )[: self.max_visual_slides]
                    if mode == "sanitized_visual"
                    else []
                )
                slide_size = (
                    _presentation_size(archive, self.max_part_bytes)
                    if visual_parts
                    else (12_192_000, 6_858_000)
                )
        except (OSError, zipfile.BadZipFile) as exc:
            raise PPTXParseError("File is not a readable PPTX archive") from exc

        color_map = parse_clr_map(resolved.slide_master_xml)
        theme = parse_theme_palette(resolved.theme_xml)
        primary = _resolve_color("accent1", theme.palette, color_map, "#2563EB")
        secondary = _resolve_color("accent2", theme.palette, color_map, primary)
        accent = _resolve_color("accent3", theme.palette, color_map, secondary)
        background = _resolve_color("bg1", theme.palette, color_map, "#FFFFFF")
        foreground = _resolve_color("tx1", theme.palette, color_map, "#111827")
        base_size_px, scale_ratio = _typography_metrics(resolved.slide_master_xml)
        layout_grammar = _layout_grammar(
            resolved.slide_master_xml, resolved.slide_layout_xml
        )

        if visual_parts:
            scheme_colors = _scheme_colors(theme.palette, color_map)
            try:
                with zipfile.ZipFile(source) as archive:
                    visual_slides = [
                        parse_sanitized_slide(
                            _read_part(archive, part_name, self.max_part_bytes), scheme_colors
                        )
                        for part_name in visual_parts
                    ]
            except SanitizedVisualParseError as exc:
                raise PPTXParseError("PPTX slide failed sanitized visual parsing") from exc
            visual_grammar, visual_primary, visual_base_px, visual_ratio = (
                _summarize_visual_slides(visual_slides, *slide_size)
            )
            layout_grammar = visual_grammar + layout_grammar
            if visual_primary and visual_primary != primary:
                previous_primary = primary
                primary = visual_primary
                if secondary == visual_primary:
                    secondary = previous_primary
            base_size_px = visual_base_px or base_size_px
            scale_ratio = visual_ratio or scale_ratio

        return VisualStyleProfileData(
            palette=Palette(
                primary=primary,
                secondary=secondary,
                accent=accent,
                background=background,
                foreground=foreground,
            ),
            typography=Typography(
                heading_font=theme.heading_font,
                body_font=theme.body_font,
                base_size_px=base_size_px,
                scale_ratio=scale_ratio,
            ),
            spacing_grid=SpacingGrid(
                base_unit_px=8,
                slide_padding_units=8,
                component_gap_units=3,
            ),
            layout_grammar=layout_grammar,
        )
