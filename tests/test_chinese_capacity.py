import pytest

from app.presentation.capacity import ChineseCapacityService, chinese_capacity_catalog
from app.presentation.capacity.service import content_fingerprint


def test_r1_catalog_has_six_page_kinds_and_twelve_chinese_variants() -> None:
    catalog = chinese_capacity_catalog

    assert len(catalog.variants) == 12
    assert {variant.page_kind for variant in catalog.variants} == {
        "cover",
        "section",
        "key_message",
        "evidence",
        "process",
        "closing",
    }
    assert {variant.status for variant in catalog.variants} == {"proposed"}
    assert catalog.font_asset_id == "font.noto-sans-sc-variable-wght"
    assert len(catalog.font_sha256) == 64


def test_capacity_prefers_pagination_instead_of_shrinking_below_floor() -> None:
    service = ChineseCapacityService()
    slot = chinese_capacity_catalog.variant("cover-standard").slot("title")

    recommended = service.decide("企业流程智能化方案", slot)
    overflow = service.decide("面" * 80, slot)

    assert recommended.action == "fit"
    assert overflow.action == "paginate"
    assert overflow.minimum_font_size_px == slot.min_font_size_px == 60
    assert overflow.error_code == "TEMPLATE_CAPACITY_EXCEEDED"


def test_verbatim_pagination_preserves_every_character_and_number() -> None:
    service = ChineseCapacityService()
    text = (
        "经审核材料确认，2025年营业收入为1438亿元。"
        "该数字只能原样展示，不得增加、删减或者推测。"
        "来源版本为V1.1，更新时间为2026年8月11日。"
    )

    pages = service.paginate_read_only(text, max_units=24)

    assert len(pages.pages) > 1
    assert "".join(pages.pages) == text
    assert pages.source_sha256 == content_fingerprint(text)
    assert "1438亿元" in "".join(pages.pages)
    assert any("1438亿元" in page for page in pages.pages)
    pages.validate()


def test_unbreakable_identifier_that_cannot_fit_is_rejected() -> None:
    service = ChineseCapacityService()
    slot = chinese_capacity_catalog.variant("process-4-5-step").slot("step_title")

    result = service.decide("ABCDEF0123456789/ABCDEF0123456789", slot)

    assert result.action == "reject"
    assert result.error_code == "UNBREAKABLE_TOKEN_OVERFLOW"


def test_read_only_pagination_never_splits_number_and_unit() -> None:
    pages = ChineseCapacityService().paginate_read_only(
        "收入确认值为1438亿元，后续内容进入下一页。", max_units=9
    )

    assert any("1438亿元" in page for page in pages.pages)
    assert "".join(pages.pages) == "收入确认值为1438亿元，后续内容进入下一页。"


def test_catalog_rejects_non_monotonic_capacity() -> None:
    source = chinese_capacity_catalog.variant("cover-standard").slot("title")

    with pytest.raises(ValueError, match="monotonic"):
        source.model_copy(
            update={"recommended": source.recommended.model_copy(update={"max_cjk_units": 40})}
        ).model_validate(
            {
                **source.model_dump(),
                "recommended": {"max_cjk_units": 40, "max_lines": 2},
            }
        )
