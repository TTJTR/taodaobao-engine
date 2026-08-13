from app.presentation.style.features import NormalizedGeometry, PageSample, StyleFeatureSet
from app.presentation.style.master_layout_mapper import map_master_layout
from app.schemas.style_profile import ArchetypeCapacity, ArchetypeSlot, LayoutArchetype

SYSTEM_GEOMETRY = {
    "title": NormalizedGeometry(x=650, y=500, width=8700, height=1100),
    "body": NormalizedGeometry(x=650, y=2000, width=8700, height=7000),
    "left": NormalizedGeometry(x=650, y=2100, width=4150, height=6800),
    "right": NormalizedGeometry(x=5200, y=2100, width=4150, height=6800),
    "card_1": NormalizedGeometry(x=650, y=2200, width=2700, height=6500),
    "card_2": NormalizedGeometry(x=3650, y=2200, width=2700, height=6500),
    "card_3": NormalizedGeometry(x=6650, y=2200, width=2700, height=6500),
}


def _classify(page: PageSample) -> str:
    if page.card_grid and len(page.boxes) >= 3:
        return "title_three_cards"
    if page.column_count >= 2:
        return "title_two_column"
    return "title_body"


def _safe_observed_boxes(page: PageSample, count: int) -> tuple[NormalizedGeometry, ...]:
    content = sorted(
        (box for box in page.boxes if box.y >= 1500 and box.width >= 1200 and box.height >= 800),
        key=lambda box: (box.y, box.x),
    )
    if len(content) < count:
        return ()
    selected = tuple(content[:count])
    for index, first in enumerate(selected):
        for second in selected[index + 1 :]:
            if not (
                first.x + first.width <= second.x
                or second.x + second.width <= first.x
                or first.y + first.height <= second.y
                or second.y + second.height <= first.y
            ):
                return ()
    return selected


def _slot(role: str, geometry: NormalizedGeometry, chars: int, lines: int) -> ArchetypeSlot:
    allowed = {
        "title": ("title",),
        "body": ("key_message", "evidence_card"),
        "left": ("key_message", "evidence_card"),
        "right": ("key_message", "evidence_card"),
        "card_1": ("key_message", "evidence_card"),
        "card_2": ("key_message", "evidence_card"),
        "card_3": ("key_message", "evidence_card"),
    }[role]
    return ArchetypeSlot(
        role=role,
        geometry=geometry,
        allowed_components=allowed,
        capacity=ArchetypeCapacity(max_characters=chars, max_lines=lines, min_font_pt=14),
    )


def cluster_layout_archetypes(features: StyleFeatureSet) -> tuple[LayoutArchetype, ...]:
    pages = features.page_samples or _pages_from_master_geometry(features)
    if not pages:
        return (_title_body(1, 0.35, None),)
    groups: dict[str, list[PageSample]] = {}
    for page in pages:
        groups.setdefault(_classify(page), []).append(page)
    total = len(pages)
    result = []
    for token, pages in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        confidence = round(min(0.95, 0.45 + len(pages) / total * 0.5), 3)
        representative = min(pages, key=lambda page: abs(page.density - 0.45))
        if token == "title_two_column":
            result.append(_two_column(len(pages), confidence, representative))
        elif token == "title_three_cards":
            result.append(_three_cards(len(pages), confidence, representative))
        else:
            result.append(_title_body(len(pages), confidence, representative))
    return tuple(result)


def _pages_from_master_geometry(features: StyleFeatureSet) -> tuple[PageSample, ...]:
    pages = []
    for index, layout in enumerate(features.master_layouts, start=1):
        mapping = map_master_layout(layout)
        boxes = tuple(
            placeholder.geometry
            for placeholder in layout.placeholders
            if placeholder.geometry is not None
        )
        if not boxes:
            continue
        columns = {
            "title_body": 1,
            "title_two_column": 2,
            "title_three_cards": 3,
        }[mapping.archetype]
        pages.append(
            PageSample(
                page_index=index,
                boxes=boxes,
                title_band=any(box.y <= 1_800 for box in boxes),
                column_count=columns,
                card_grid=mapping.archetype == "title_three_cards",
                image_region=False,
                density=min(1, sum(box.width * box.height for box in boxes) / 100_000_000),
            )
        )
    return tuple(pages)


def _title_body(frequency: int, confidence: float, page: PageSample | None) -> LayoutArchetype:
    boxes = _safe_observed_boxes(page, 1) if page else ()
    body = boxes[0] if boxes else SYSTEM_GEOMETRY["body"]
    return LayoutArchetype(
        archetype_token="title_body",
        purpose="content",
        frequency=frequency,
        confidence=confidence,
        slots=(
            _slot("title", SYSTEM_GEOMETRY["title"], 120, 2),
            _slot("body", body, 1200, 20),
        ),
        preferred_skin_tokens=("plain_text",),
        fallback_archetype="title_body",
    )


def _two_column(frequency: int, confidence: float, page: PageSample) -> LayoutArchetype:
    boxes = _safe_observed_boxes(page, 2)
    left, right = boxes if boxes else (SYSTEM_GEOMETRY["left"], SYSTEM_GEOMETRY["right"])
    return LayoutArchetype(
        archetype_token="title_two_column",
        purpose="comparison",
        frequency=frequency,
        confidence=confidence,
        slots=(
            _slot("title", SYSTEM_GEOMETRY["title"], 120, 2),
            _slot("left", left, 700, 18),
            _slot("right", right, 700, 18),
        ),
        preferred_skin_tokens=("outline_card",),
        fallback_archetype="title_body",
    )


def _three_cards(frequency: int, confidence: float, page: PageSample) -> LayoutArchetype:
    boxes = _safe_observed_boxes(page, 3)
    card_boxes = boxes or tuple(SYSTEM_GEOMETRY[f"card_{index}"] for index in range(1, 4))
    return LayoutArchetype(
        archetype_token="title_three_cards",
        purpose="evidence",
        frequency=frequency,
        confidence=confidence,
        slots=(
            _slot("title", SYSTEM_GEOMETRY["title"], 120, 2),
            *tuple(
                _slot(f"card_{index}", box, 420, 14)
                for index, box in enumerate(card_boxes, start=1)
            ),
        ),
        preferred_skin_tokens=("filled_card",),
        fallback_archetype="title_two_column",
    )
