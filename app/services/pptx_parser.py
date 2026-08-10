from __future__ import annotations

import posixpath
import zipfile
from colorsys import hls_to_rgb, rgb_to_hls
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from xml.etree import ElementTree

from app.schemas.presentation import (
    Palette,
    SpacingGrid,
    Typography,
    VisualStyleProfileData,
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


class SafePPTXParser:
    """Extract style metadata without opening ordinary slide XML parts."""

    def __init__(self, *, max_part_bytes: int = 2 * 1024 * 1024) -> None:
        self.max_part_bytes = max_part_bytes

    def parse(self, source: str | Path | BinaryIO) -> VisualStyleProfileData:
        try:
            with zipfile.ZipFile(source) as archive:
                resolved = resolve_theme_xml(archive, self.max_part_bytes)
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
            layout_grammar=_layout_grammar(
                resolved.slide_master_xml, resolved.slide_layout_xml
            ),
        )
