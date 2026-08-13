from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int


CANVAS = Box(0, 0, 1600, 900)
SAFE = Box(96, 72, 1408, 756)

# Frozen R1 geometry. Master/layout names never participate in selection.
VARIANT_GEOMETRY: dict[str, dict[str, Box]] = {
    "cover-standard": {
        "eyebrow": Box(112, 112, 920, 32),
        "title": Box(112, 212, 1040, 230),
        "subtitle": Box(116, 482, 760, 104),
        "metadata": Box(116, 748, 680, 28),
    },
    "cover-long-title": {
        "title": Box(112, 188, 1050, 260),
        "subtitle": Box(116, 492, 820, 120),
        "metadata": Box(116, 748, 680, 28),
    },
    "section-standard": {
        "number": Box(112, 152, 260, 34),
        "title": Box(112, 300, 1050, 190),
    },
    "section-with-lead": {
        "title": Box(112, 232, 1040, 170),
        "lead": Box(116, 466, 880, 130),
    },
    "key-message-statement": {
        "title": Box(112, 96, 1040, 104),
        "statement": Box(112, 286, 1180, 250),
        "source": Box(112, 742, 1130, 46),
    },
    "key-message-with-support": {
        "title": Box(112, 88, 1040, 96),
        "statement": Box(112, 236, 1110, 190),
        "support": Box(112, 486, 940, 170),
        "source": Box(112, 742, 1130, 46),
    },
    "evidence-single": {
        "title": Box(112, 84, 1060, 94),
        "evidence_heading": Box(112, 236, 980, 70),
        "verbatim": Box(112, 332, 1020, 260),
        "source": Box(112, 692, 1050, 54),
        "version": Box(112, 758, 1000, 30),
    },
    "evidence-continuation": {
        "title": Box(112, 88, 1060, 90),
        "verbatim": Box(112, 242, 1040, 360),
        "source": Box(112, 704, 1050, 58),
    },
    "process-3-step": {
        "title": Box(112, 82, 1040, 94),
        "steps": Box(112, 262, 1376, 420),
    },
    "process-4-5-step": {
        "title": Box(112, 82, 1040, 94),
        "steps": Box(112, 248, 1376, 444),
    },
    "closing-takeaway": {
        "title": Box(112, 220, 1080, 180),
        "summary": Box(116, 462, 900, 150),
        "signature": Box(116, 742, 700, 32),
    },
    "closing-next-action": {
        "title": Box(112, 170, 1080, 170),
        "action": Box(112, 410, 980, 110),
        "support": Box(112, 572, 900, 116),
    },
}


def validate_geometry() -> None:
    for variant_id, slots in VARIANT_GEOMETRY.items():
        for slot_name, box in slots.items():
            if box.width <= 0 or box.height <= 0:
                raise ValueError(f"invalid box: {variant_id}.{slot_name}")
            if box.x < 0 or box.y < 0 or box.x + box.width > CANVAS.width:
                raise ValueError(f"horizontal overflow: {variant_id}.{slot_name}")
            if box.y + box.height > CANVAS.height:
                raise ValueError(f"vertical overflow: {variant_id}.{slot_name}")


validate_geometry()
