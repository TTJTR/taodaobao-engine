from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass

from app.presentation.capacity.models import CapacityDecision, SlotCapacity

UNBREAKABLE_PATTERN = re.compile(
    r"(?:\d{4}年\d{1,2}月\d{1,2}日|\d+(?:\.\d+)?(?:%|亿元|万元|元|万|亿|GB|TB|ms|秒)|[A-Z][A-Z0-9._/-]{7,})"
)


def _unit(character: str) -> float:
    if character.isspace():
        return 0.3
    if "\u4e00" <= character <= "\u9fff" or "\u3400" <= character <= "\u4dbf":
        return 1.0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 0.8
    if character.isupper():
        return 0.65
    if character.islower() or character.isdigit():
        return 0.55
    return 0.35


def count_cjk_units(text: str) -> float:
    return round(sum(_unit(character) for character in text), 2)


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReadOnlyTextPages:
    source_text: str
    pages: tuple[str, ...]
    source_sha256: str

    def validate(self) -> None:
        if "".join(self.pages) != self.source_text:
            raise ValueError("FACT_CONTENT_CHANGED_DURING_PAGINATION")
        if content_fingerprint(self.source_text) != self.source_sha256:
            raise ValueError("FACT_CONTENT_FINGERPRINT_MISMATCH")


class ChineseCapacityService:
    """Select a capacity action without rewriting text or shrinking below the floor."""

    def decide(
        self,
        text: str,
        capacity: SlotCapacity,
        *,
        content_mode: str = "label_only",
    ) -> CapacityDecision:
        units = count_cjk_units(text)
        longest_token = max(
            (count_cjk_units(item) for item in UNBREAKABLE_PATTERN.findall(text)), default=0
        )
        hard_units_per_line = (
            capacity.hard_limit.max_cjk_units / capacity.hard_limit.max_lines
        )
        estimated_lines = math.ceil(units / max(1, hard_units_per_line))
        if longest_token > capacity.hard_limit.max_cjk_units / capacity.hard_limit.max_lines:
            return CapacityDecision(
                action="reject",
                tier="overflow",
                cjk_units=units,
                estimated_lines=estimated_lines,
                minimum_font_size_px=capacity.min_font_size_px,
                error_code="UNBREAKABLE_TOKEN_OVERFLOW",
            )
        if (
            units <= capacity.recommended.max_cjk_units
            and estimated_lines <= capacity.recommended.max_lines
        ):
            return self._decision("fit", "recommended", units, estimated_lines, capacity)
        if (
            units <= capacity.soft_limit.max_cjk_units
            and estimated_lines <= capacity.soft_limit.max_lines
        ):
            return self._decision("use_compact_variant", "soft", units, estimated_lines, capacity)
        if (
            units <= capacity.hard_limit.max_cjk_units
            and estimated_lines <= capacity.hard_limit.max_lines
        ):
            return self._decision("paginate", "hard", units, estimated_lines, capacity)
        error = (
            "VERBATIM_REQUIRES_PAGINATION"
            if content_mode == "verbatim"
            else "TEMPLATE_CAPACITY_EXCEEDED"
        )
        return CapacityDecision(
            action="paginate" if capacity.pagination_allowed else "reject",
            tier="overflow",
            cjk_units=units,
            estimated_lines=estimated_lines,
            minimum_font_size_px=capacity.min_font_size_px,
            error_code=error,
        )

    def paginate_read_only(self, text: str, max_units: float) -> ReadOnlyTextPages:
        if max_units <= 0:
            raise ValueError("max_units must be positive")
        pages: list[str] = []
        protected = tuple(UNBREAKABLE_PATTERN.finditer(text))
        start = 0
        current_units = 0.0
        preferred_break = -1
        for index, character in enumerate(text):
            current_units += _unit(character)
            if character in "。！？；\n":
                preferred_break = index + 1
            if current_units <= max_units:
                continue
            token = next(
                (match for match in protected if match.start() <= index < match.end()), None
            )
            if token is not None:
                if count_cjk_units(token.group()) > max_units:
                    raise ValueError("UNBREAKABLE_TOKEN_OVERFLOW")
                if token.start() > start:
                    split_at = token.start()
                elif index + 1 < token.end():
                    continue
                else:
                    split_at = token.end()
            else:
                split_at = preferred_break if preferred_break > start else index
            if split_at == start:
                split_at = index + 1
            pages.append(text[start:split_at])
            start = split_at
            current_units = count_cjk_units(text[start : index + 1])
            preferred_break = preferred_break if preferred_break > start else -1
        if start < len(text):
            pages.append(text[start:])
        result = ReadOnlyTextPages(
            source_text=text,
            pages=tuple(pages),
            source_sha256=content_fingerprint(text),
        )
        result.validate()
        return result

    @staticmethod
    def _decision(action, tier, units, lines, capacity) -> CapacityDecision:
        return CapacityDecision(
            action=action,
            tier=tier,
            cjk_units=units,
            estimated_lines=lines,
            minimum_font_size_px=capacity.min_font_size_px,
        )
