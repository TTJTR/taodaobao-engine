from app.presentation.capacity.models import (
    CapacityBand,
    ChineseCapacityCatalog,
    ChineseTemplateVariant,
    SlotCapacity,
)


def _band(units: float, lines: int) -> CapacityBand:
    return CapacityBand(max_cjk_units=units, max_lines=lines)


def _slot(
    name: str,
    role: str,
    size: int,
    minimum: int,
    weight: int,
    recommended: tuple[float, int],
    soft: tuple[float, int],
    hard: tuple[float, int],
    *,
    pagination: bool = True,
) -> SlotCapacity:
    return SlotCapacity(
        slot_name=name,
        font_role=role,
        font_size_px=size,
        min_font_size_px=minimum,
        font_weight=weight,
        line_height=1.12 if role in {"display", "title", "statement"} else 1.35,
        recommended=_band(*recommended),
        soft_limit=_band(*soft),
        hard_limit=_band(*hard),
        pagination_allowed=pagination,
    )


chinese_capacity_catalog = ChineseCapacityCatalog(
    variants=(
        ChineseTemplateVariant(
            variant_id="cover-standard",
            page_kind="cover",
            slots=(
                _slot("eyebrow", "metadata", 18, 14, 600, (10, 1), (14, 1), (18, 1)),
                _slot("title", "display", 92, 60, 600, (18, 2), (24, 2), (32, 3)),
                _slot("subtitle", "statement", 32, 24, 400, (28, 2), (40, 2), (54, 3)),
                _slot("metadata", "metadata", 18, 14, 500, (18, 1), (24, 1), (32, 1)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="cover-long-title",
            page_kind="cover",
            slots=(
                _slot("title", "display", 76, 60, 600, (24, 2), (28, 2), (32, 3)),
                _slot("subtitle", "statement", 30, 24, 400, (32, 2), (44, 3), (54, 3)),
                _slot("metadata", "metadata", 18, 14, 500, (18, 1), (24, 1), (32, 1)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="section-standard",
            page_kind="section",
            slots=(
                _slot("number", "metadata", 20, 14, 600, (6, 1), (8, 1), (12, 1)),
                _slot("title", "display", 78, 56, 500, (12, 2), (18, 2), (24, 2)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="section-with-lead",
            page_kind="section",
            slots=(
                _slot("title", "display", 72, 56, 500, (12, 2), (18, 2), (24, 2)),
                _slot("lead", "statement", 30, 24, 400, (30, 2), (44, 3), (60, 3)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="key-message-statement",
            page_kind="key_message",
            slots=(
                _slot("title", "title", 44, 36, 600, (16, 2), (22, 2), (28, 2)),
                _slot("statement", "statement", 40, 28, 500, (34, 3), (48, 4), (64, 4)),
                _slot("source", "metadata", 18, 14, 500, (30, 1), (44, 2), (64, 2)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="key-message-with-support",
            page_kind="key_message",
            slots=(
                _slot("title", "title", 42, 36, 600, (16, 2), (22, 2), (28, 2)),
                _slot("statement", "statement", 34, 28, 500, (34, 3), (48, 4), (64, 4)),
                _slot("support", "body", 24, 20, 400, (64, 4), (90, 5), (120, 6)),
                _slot("source", "metadata", 18, 14, 500, (30, 1), (44, 2), (64, 2)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="evidence-single",
            page_kind="evidence",
            slots=(
                _slot("title", "title", 42, 36, 600, (16, 2), (22, 2), (28, 2)),
                _slot("evidence_heading", "statement", 30, 24, 600, (18, 2), (26, 2), (34, 3)),
                _slot("verbatim", "evidence", 26, 20, 400, (90, 6), (130, 8), (180, 10)),
                _slot("source", "metadata", 18, 14, 500, (26, 2), (40, 2), (56, 3)),
                _slot("version", "metadata", 16, 14, 500, (28, 1), (40, 2), (52, 2)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="evidence-continuation",
            page_kind="evidence",
            slots=(
                _slot("title", "title", 38, 36, 600, (18, 2), (24, 2), (30, 2)),
                _slot("verbatim", "evidence", 24, 20, 400, (110, 7), (150, 9), (180, 10)),
                _slot("source", "metadata", 18, 14, 500, (30, 2), (44, 2), (56, 3)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="process-3-step",
            page_kind="process",
            slots=(
                _slot("title", "title", 42, 36, 600, (16, 2), (22, 2), (28, 2)),
                _slot("step_title", "statement", 28, 22, 600, (8, 2), (12, 2), (16, 2)),
                _slot("step_body", "body", 24, 18, 400, (26, 3), (38, 4), (52, 5)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="process-4-5-step",
            page_kind="process",
            slots=(
                _slot("title", "title", 40, 36, 600, (16, 2), (22, 2), (28, 2)),
                _slot("step_title", "statement", 24, 20, 600, (8, 2), (12, 2), (16, 2)),
                _slot("step_body", "body", 21, 18, 400, (26, 3), (38, 4), (52, 5)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="closing-takeaway",
            page_kind="closing",
            slots=(
                _slot("title", "display", 76, 56, 500, (18, 2), (24, 2), (32, 3)),
                _slot("summary", "statement", 30, 24, 400, (48, 3), (70, 4), (90, 5)),
                _slot("signature", "metadata", 18, 14, 500, (24, 1), (32, 1), (42, 1)),
            ),
        ),
        ChineseTemplateVariant(
            variant_id="closing-next-action",
            page_kind="closing",
            slots=(
                _slot("title", "display", 68, 56, 500, (18, 2), (24, 2), (32, 3)),
                _slot("action", "statement", 34, 24, 500, (30, 2), (44, 3), (60, 3)),
                _slot("support", "body", 24, 20, 400, (48, 3), (70, 4), (90, 5)),
            ),
        ),
    )
)
