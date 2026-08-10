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

# Relationship traversal, clrMap handling and theme parsing are ported from
# allweonedev/presentation-ai@43fe74a (MIT). See THIRD_PARTY_NOTICES.md.

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
    return ResolvedTheme(theme_xml=theme_xml, slide_master_xml=master_xml)


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


def parse_theme_palette(theme_xml: bytes) -> ParsedTheme:
    root = _parse_xml(theme_xml)
    color_scheme = _find_first(root, "clrScheme")
    palette = {
        slot: _color_from_slot(_find_child(color_scheme, slot)) for slot in THEME_COLOR_SLOTS
    }

    font_scheme = _find_first(root, "fontScheme")
    major_font = _find_first(font_scheme, "majorFont")
    minor_font = _find_first(font_scheme, "minorFont")
    major_latin = _find_first(major_font, "latin")
    minor_latin = _find_first(minor_font, "latin")
    return ParsedTheme(
        palette=palette,
        heading_font=_map_font(major_latin.get("typeface") if major_latin is not None else None),
        body_font=_map_font(minor_latin.get("typeface") if minor_latin is not None else None),
    )


def _resolve_color(
    logical_name: str,
    palette: dict[str, str | None],
    color_map: dict[str, str],
    fallback: str,
) -> str:
    return palette.get(color_map.get(logical_name, "")) or fallback


def _master_layout_grammar(slide_master_xml: bytes) -> list[str]:
    root = _parse_xml(slide_master_xml)
    placeholders: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "ph":
            continue
        placeholder = element.get("type", "body")
        if placeholder not in placeholders:
            placeholders.append(placeholder)
    tokens = [f"master-placeholder:{placeholder}" for placeholder in placeholders[:8]]
    return tokens or ["master-placeholder:unspecified"]


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
                base_size_px=18,
                scale_ratio=1.25,
            ),
            spacing_grid=SpacingGrid(
                base_unit_px=8,
                slide_padding_units=8,
                component_gap_units=3,
            ),
            layout_grammar=_master_layout_grammar(resolved.slide_master_xml),
        )
