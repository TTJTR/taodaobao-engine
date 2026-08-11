from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.presentation.style.features import MasterLayoutFeature, NormalizedGeometry

MasterArchetype = Literal["title_body", "title_two_column", "title_three_cards"]


class MasterLayoutMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_name: str
    archetype: MasterArchetype
    confidence: float = Field(ge=0, le=1)
    reason: Literal[
        "placeholder_geometry_three_columns",
        "placeholder_geometry_two_columns",
        "placeholder_geometry_body",
        "system_fallback_no_geometry",
    ]


def map_master_layout(layout: MasterLayoutFeature) -> MasterLayoutMapping:
    """Treat arbitrary master names as audit metadata, never as classification input."""
    boxes = tuple(item.geometry for item in layout.placeholders if item.geometry is not None)
    content = tuple(box for box in boxes if not _looks_like_title(box))
    if _forms_columns(content, 3):
        return MasterLayoutMapping(
            source_name=layout.name,
            archetype="title_three_cards",
            confidence=0.78,
            reason="placeholder_geometry_three_columns",
        )
    if _forms_columns(content, 2):
        return MasterLayoutMapping(
            source_name=layout.name,
            archetype="title_two_column",
            confidence=0.76,
            reason="placeholder_geometry_two_columns",
        )
    if content:
        return MasterLayoutMapping(
            source_name=layout.name,
            archetype="title_body",
            confidence=0.64,
            reason="placeholder_geometry_body",
        )
    return MasterLayoutMapping(
        source_name=layout.name,
        archetype="title_body",
        confidence=0.3,
        reason="system_fallback_no_geometry",
    )


def _looks_like_title(box: NormalizedGeometry) -> bool:
    return box.y <= 1_800 and box.height <= 2_200 and box.width >= 3_500


def _forms_columns(boxes: tuple[NormalizedGeometry, ...], count: int) -> bool:
    candidates = sorted(
        (box for box in boxes if box.width >= 1_200 and box.height >= 1_200),
        key=lambda box: box.x,
    )
    if len(candidates) != count:
        return False
    widths = [box.width for box in candidates]
    if max(widths) / min(widths) > 1.45:
        return False
    for left, right in zip(candidates, candidates[1:], strict=False):
        if left.x + left.width > right.x:
            return False
        vertical_overlap = min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
        if vertical_overlap < min(left.height, right.height) * 0.55:
            return False
    return True
