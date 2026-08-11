import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.presentation.render_ir.font_metrics import (
    FontFaceMetrics,
    FontMetricsProvider,
    OpenTypeFontMetricsProvider,
)
from app.presentation.render_ir.models import LineBox, TextLayout


class TextLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=8_000)
    requested_font: str = Field(min_length=1, max_length=100)
    fallback_fonts: tuple[str, ...] = ("Noto Sans CJK SC", "Arial")
    font_size: float = Field(ge=10, le=96)
    min_font_size: float = Field(default=12, ge=10, le=96)
    line_height_ratio: float = Field(default=1.25, ge=1, le=2)
    box_width: float = Field(gt=0)
    box_height: float = Field(gt=0)


@dataclass(frozen=True)
class _MeasuredLine:
    text: str
    start: int
    end: int


class TextLayoutEngine:
    """Deterministic layout kernel; Renderers consume its frozen line boxes."""

    version = "text-layout-v2"

    def __init__(self, font_metrics: FontMetricsProvider | None = None) -> None:
        self.font_metrics = font_metrics or OpenTypeFontMetricsProvider()

    def layout(self, request: TextLayoutRequest) -> TextLayout:
        face = self.font_metrics.resolve(
            (request.requested_font, *request.fallback_fonts), request.text
        )
        size = request.font_size
        while True:
            lines = self._wrap(request.text, request.box_width, size, face)
            line_height = round(size * request.line_height_ratio, 4)
            if len(lines) * line_height <= request.box_height or size <= request.min_font_size:
                break
            size = max(request.min_font_size, size - 1)

        overflowed = len(lines) * line_height > request.box_height or any(
            self._measure(line.text, size, face) > request.box_width for line in lines
        )
        boxes = tuple(
            LineBox(
                text=line.text,
                x=0,
                y=round(index * line_height, 4),
                width=round(self._measure(line.text, size, face), 4),
                height=line_height,
                start_offset=line.start,
                end_offset=line.end,
            )
            for index, line in enumerate(lines)
        )
        return TextLayout(
            engine_version=self.version,
            measurement_method="opentype" if face is not None else "conservative",
            requested_font=request.requested_font,
            resolved_font=face.family if face is not None else request.requested_font,
            font_file_hash=face.file_hash if face is not None else None,
            font_size=size,
            line_height=line_height,
            line_breaks=tuple(line.end for line in lines[:-1]),
            line_boxes=boxes,
            overflowed=overflowed,
        )

    def _wrap(
        self,
        text: str,
        width: float,
        font_size: float,
        face: FontFaceMetrics | None,
    ) -> tuple[_MeasuredLine, ...]:
        lines: list[_MeasuredLine] = []
        start = 0
        current = ""
        current_end = 0
        for token, token_start, token_end in self._tokens(text):
            candidate = current + token
            if current and self._measure(candidate, font_size, face) > width:
                lines.append(_MeasuredLine(current, start, current_end))
                start = token_start
                current = token
            else:
                current = candidate
            current_end = token_end
        lines.append(_MeasuredLine(current, start, current_end))
        return tuple(lines)

    @staticmethod
    def _tokens(text: str):
        pattern = re.compile(
            r"\d+(?:[.,]\d+)*(?:%|亿元|万元|元)?|[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*|.",
            re.DOTALL,
        )
        return tuple(
            (match.group(), match.start(), match.end()) for match in pattern.finditer(text)
        )

    @staticmethod
    def _measure(text: str, font_size: float, face: FontFaceMetrics | None) -> float:
        if face is not None:
            return face.measure(text, font_size)
        units = sum(1.0 if ord(char) > 0x7F else 0.58 for char in text)
        return units * font_size
