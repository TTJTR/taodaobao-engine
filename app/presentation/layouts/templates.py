from app.presentation.layouts.models import Capacity, LayoutTemplate, Slot
from app.schemas.presentation import ComponentGeometry


def _slot(
    name: str,
    geometry: tuple[int, int, int, int],
    allowed: tuple[str, ...],
    *,
    chars: int,
    lines: int,
    style: str,
) -> Slot:
    return Slot(
        name=name,
        geometry=ComponentGeometry(
            x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3]
        ),
        allowed_component_types=allowed,
        capacity=Capacity(
            max_components=1,
            max_characters=chars,
            max_lines=lines,
            min_font_px=16,
        ),
        text_style_token=style,
    )


LAYOUT_TEMPLATES = (
    LayoutTemplate(
        token="cover",
        slots=(
            _slot(
                "title", (900, 2100, 8200, 2300), ("title",), chars=100, lines=3, style="display"
            ),
            _slot(
                "message",
                (1300, 5200, 7400, 1400),
                ("key_message",),
                chars=180,
                lines=4,
                style="body",
            ),
        ),
        fallback_token="title_body",
    ),
    LayoutTemplate(
        token="title_body",
        slots=(
            _slot("title", (650, 550, 8700, 1200), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "body",
                (650, 2150, 8700, 6800),
                ("key_message", "evidence_card"),
                chars=1200,
                lines=20,
                style="body",
            ),
        ),
    ),
    LayoutTemplate(
        token="two_column",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "left",
                (650, 2100, 4150, 7000),
                ("key_message", "evidence_card"),
                chars=700,
                lines=18,
                style="evidence",
            ),
            _slot(
                "right",
                (5200, 2100, 4150, 7000),
                ("key_message", "evidence_card"),
                chars=700,
                lines=18,
                style="evidence",
            ),
        ),
        fallback_token="title_body",
    ),
    LayoutTemplate(
        token="three_cards",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "card_1",
                (650, 2250, 2700, 6500),
                ("key_message", "evidence_card"),
                chars=420,
                lines=14,
                style="evidence",
            ),
            _slot(
                "card_2",
                (3650, 2250, 2700, 6500),
                ("key_message", "evidence_card"),
                chars=420,
                lines=14,
                style="evidence",
            ),
            _slot(
                "card_3",
                (6650, 2250, 2700, 6500),
                ("key_message", "evidence_card"),
                chars=420,
                lines=14,
                style="evidence",
            ),
        ),
        fallback_token="two_column",
    ),
    LayoutTemplate(
        token="evidence_grid",
        slots=(
            _slot("title", (650, 500, 8700, 1000), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "evidence_1",
                (650, 1900, 4150, 3300),
                ("evidence_card",),
                chars=360,
                lines=10,
                style="evidence",
            ),
            _slot(
                "evidence_2",
                (5200, 1900, 4150, 3300),
                ("evidence_card",),
                chars=360,
                lines=10,
                style="evidence",
            ),
            _slot(
                "evidence_3",
                (650, 5550, 4150, 3300),
                ("evidence_card",),
                chars=360,
                lines=10,
                style="evidence",
            ),
            _slot(
                "evidence_4",
                (5200, 5550, 4150, 3300),
                ("evidence_card",),
                chars=360,
                lines=10,
                style="evidence",
            ),
        ),
        fallback_token="three_cards",
    ),
    LayoutTemplate(
        token="metric_highlight",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "metric", (1450, 2450, 7100, 5200), ("metric",), chars=420, lines=8, style="display"
            ),
        ),
        fallback_token="title_body",
    ),
    LayoutTemplate(
        token="comparison",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "comparison",
                (650, 2050, 8700, 7000),
                ("comparison",),
                chars=1400,
                lines=20,
                style="body",
            ),
        ),
        fallback_token="two_column",
    ),
    LayoutTemplate(
        token="timeline",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "timeline",
                (650, 2150, 8700, 6700),
                ("timeline",),
                chars=1800,
                lines=24,
                style="body",
            ),
        ),
        fallback_token="title_body",
    ),
    LayoutTemplate(
        token="process",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "process", (650, 2150, 8700, 6700), ("process",), chars=1800, lines=24, style="body"
            ),
        ),
        fallback_token="title_body",
    ),
    LayoutTemplate(
        token="source_list",
        slots=(
            _slot("title", (650, 550, 8700, 1100), ("title",), chars=120, lines=2, style="heading"),
            _slot(
                "sources",
                (1000, 2050, 8000, 6800),
                ("source_list",),
                chars=1400,
                lines=18,
                style="body",
            ),
        ),
        fallback_token="title_body",
    ),
)
