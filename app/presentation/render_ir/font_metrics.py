from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from fontTools.ttLib import TTFont, TTLibError


@dataclass(frozen=True)
class FontFaceMetrics:
    family: str
    file_path: Path
    file_hash: str
    units_per_em: int
    advances: dict[int, int]

    def supports(self, text: str) -> bool:
        return all(char.isspace() or ord(char) in self.advances for char in text)

    def measure(self, text: str, font_size: float) -> float:
        space_advance = self.advances.get(ord(" "), self.units_per_em // 3)
        units = sum(
            space_advance if char.isspace() else self.advances[ord(char)] for char in text
        )
        return units / self.units_per_em * font_size


class FontMetricsProvider(Protocol):
    def resolve(self, families: tuple[str, ...], text: str) -> FontFaceMetrics | None: ...


class OpenTypeFontMetricsProvider:
    """Resolve a single font that covers all text and freeze its OpenType advances."""

    _WINDOWS_ALIASES = {
        "arial": "arial.ttf",
        "calibri": "calibri.ttf",
        "dengxian": "Deng.ttf",
        "microsoftyahei": "msyh.ttc",
        "microsoftyaheilight": "msyhl.ttc",
        "microsoftyaheiui": "msyh.ttc",
        "simhei": "simhei.ttf",
        "simsun": "simsun.ttc",
        "宋体": "simsun.ttc",
        "微软雅黑": "msyh.ttc",
        "微软雅黑light": "msyhl.ttc",
        "微软雅黑ui": "msyh.ttc",
        "黑体": "simhei.ttf",
        "等线": "Deng.ttf",
    }

    def __init__(self, search_roots: tuple[Path, ...] | None = None) -> None:
        self.search_roots = search_roots or self._default_roots()

    def resolve(self, families: tuple[str, ...], text: str) -> FontFaceMetrics | None:
        for family in families:
            path = self._find_font(family)
            if path is None:
                continue
            face = self._load_face(path, family)
            if face is not None and face.supports(text):
                return face
        return None

    def _find_font(self, family: str) -> Path | None:
        normalized = self._normalize(family)
        candidates = []
        alias = self._WINDOWS_ALIASES.get(normalized)
        if alias:
            candidates.append(alias)
        if Path(family).name == family and not any(char in family for char in ("/", "\\")):
            candidates.extend((f"{family}.ttf", f"{family}.otf", f"{family}.ttc"))
        for root in self.search_roots:
            for candidate in candidates:
                path = root / candidate
                if path.is_file():
                    return path
            if root.is_dir():
                for path in root.iterdir():
                    if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                        continue
                    if self._normalize(path.stem) == normalized:
                        return path
        return None

    @staticmethod
    @lru_cache(maxsize=8)
    def _load_face(path: Path, requested_family: str) -> FontFaceMetrics | None:
        try:
            payload = path.read_bytes()
            font = TTFont(path, fontNumber=0, lazy=False)
            cmap = font.getBestCmap() or {}
            metrics = font["hmtx"].metrics
            units_per_em = font["head"].unitsPerEm
            advances = {
                codepoint: metrics[glyph_name][0]
                for codepoint, glyph_name in cmap.items()
                if glyph_name in metrics
            }
            font.close()
            return FontFaceMetrics(
                family=requested_family,
                file_path=path,
                file_hash=hashlib.sha256(payload).hexdigest(),
                units_per_em=units_per_em,
                advances=advances,
            )
        except (KeyError, OSError, TTLibError):
            return None

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(char for char in value.casefold() if char.isalnum())

    @staticmethod
    def _default_roots() -> tuple[Path, ...]:
        roots = []
        windows = os.environ.get("WINDIR")
        if windows:
            roots.append(Path(windows) / "Fonts")
        roots.extend(
            (
                Path("/usr/share/fonts/truetype/dejavu"),
                Path("/usr/share/fonts/truetype/liberation2"),
                Path("/usr/local/share/fonts"),
            )
        )
        return tuple(dict.fromkeys(roots))
