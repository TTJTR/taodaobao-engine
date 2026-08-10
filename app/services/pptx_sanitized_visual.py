from __future__ import annotations

import io
from dataclasses import dataclass, field
from xml.sax import SAXException
from xml.sax.handler import ContentHandler, feature_namespaces

from defusedxml import sax
from defusedxml.common import DefusedXmlException


class SanitizedVisualParseError(ValueError):
    pass


def _attr(attributes, name: str) -> str | None:
    for (_, local_name), value in attributes.items():
        if local_name == name:
            return value
    return None


def _number(attributes, name: str) -> float:
    try:
        return float(_attr(attributes, name) or 0)
    except ValueError:
        return 0


@dataclass
class SanitizedShape:
    kind: str
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    fill_colors: list[str] = field(default_factory=list)
    text_colors: list[str] = field(default_factory=list)
    line_colors: list[str] = field(default_factory=list)
    line_widths: list[float] = field(default_factory=list)
    font_sizes_pt: list[float] = field(default_factory=list)
    hidden: bool = False


@dataclass(frozen=True)
class SanitizedSlide:
    shapes: tuple[SanitizedShape, ...]


class _VisualWhitelistHandler(ContentHandler):
    allowed_shapes = {"sp", "graphicFrame", "pic"}
    blocked_subtrees = {
        "chartSpace",
        "pt",
        "v",
        "notes",
        "notesSlide",
        "oleObj",
        "embeddedObject",
        "control",
    }

    def __init__(self, scheme_colors: dict[str, str]) -> None:
        super().__init__()
        self.scheme_colors = scheme_colors
        self.shapes: list[SanitizedShape] = []
        self.current: SanitizedShape | None = None
        self.shape_depth = 0
        self.blocked_depth = 0
        self.stack: list[str] = []
        self.captured_xfrm = False

    def startElementNS(self, name, qname, attributes) -> None:  # noqa: N802
        local_name = name[1]
        self.stack.append(local_name)
        if self.blocked_depth or local_name in self.blocked_subtrees:
            self.blocked_depth += 1
            return
        if self.current is None and local_name in self.allowed_shapes:
            self.current = SanitizedShape(kind=local_name)
            self.shape_depth = len(self.stack)
            self.captured_xfrm = False
            return
        if self.current is None:
            return

        if local_name == "cNvPr" and (_attr(attributes, "hidden") in {"1", "true"}):
            self.current.hidden = True
        elif local_name == "xfrm" and not self.captured_xfrm:
            self.captured_xfrm = True
        elif local_name == "off" and self.captured_xfrm:
            self.current.x = _number(attributes, "x")
            self.current.y = _number(attributes, "y")
        elif local_name == "ext" and self.captured_xfrm:
            self.current.width = _number(attributes, "cx")
            self.current.height = _number(attributes, "cy")
        elif local_name in {"rPr", "defRPr", "endParaRPr"}:
            size = _number(attributes, "sz")
            if size > 0:
                self.current.font_sizes_pt.append(size / 100)
        elif local_name == "ln":
            width = _number(attributes, "w")
            if width > 0:
                self.current.line_widths.append(width)
        elif local_name in {"srgbClr", "sysClr", "schemeClr"}:
            color = self._resolve_color(local_name, attributes)
            if color:
                self._append_color(color)

    def endElementNS(self, name, qname) -> None:  # noqa: N802
        local_name = name[1]
        if self.blocked_depth:
            self.blocked_depth -= 1
            self.stack.pop()
            return
        if (
            self.current is not None
            and local_name == self.current.kind
            and len(self.stack) == self.shape_depth
        ):
            if not self.current.hidden and self.current.width > 0 and self.current.height > 0:
                self.shapes.append(self.current)
            self.current = None
            self.shape_depth = 0
            self.captured_xfrm = False
        self.stack.pop()

    def characters(self, content: str) -> None:
        # Intentionally discard all character data, including a:t and c:v values.
        return

    def _resolve_color(self, kind: str, attributes) -> str | None:
        if kind == "srgbClr":
            value = _attr(attributes, "val")
            return f"#{value.upper()}" if value and len(value) == 6 else None
        if kind == "sysClr":
            value = _attr(attributes, "lastClr")
            return f"#{value.upper()}" if value and len(value) == 6 else None
        slot = _attr(attributes, "val")
        return self.scheme_colors.get(slot or "")

    def _append_color(self, color: str) -> None:
        if self.current is None or "solidFill" not in self.stack:
            return
        if "ln" in self.stack[self.shape_depth :]:
            self.current.line_colors.append(color)
        elif any(name in self.stack for name in ("rPr", "defRPr", "endParaRPr")):
            self.current.text_colors.append(color)
        else:
            self.current.fill_colors.append(color)


def parse_sanitized_slide(xml_content: bytes, scheme_colors: dict[str, str]) -> SanitizedSlide:
    """Parse visual attributes only; character data is never retained."""
    handler = _VisualWhitelistHandler(scheme_colors)
    parser = sax.make_parser()
    parser.setFeature(feature_namespaces, True)
    parser.setContentHandler(handler)
    try:
        parser.parse(io.BytesIO(xml_content))
    except (DefusedXmlException, SAXException) as exc:
        raise SanitizedVisualParseError("Slide XML failed secure visual parsing") from exc
    return SanitizedSlide(shapes=tuple(handler.shapes))
