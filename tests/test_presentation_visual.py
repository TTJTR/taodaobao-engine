import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app.presentation.layouts.engine import LayoutEngine
from app.schemas.presentation import PresentationSpecData, VisualStyleProfileData
from app.services.html_renderer import HTMLRenderer


def _browser_path() -> str | None:
    configured = os.environ.get("PRESENTATION_TEST_BROWSER")
    candidates = (
        configured,
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _binding(index: int) -> dict:
    return {
        "claim_id": uuid.uuid4(),
        "claim_key": f"verified:{index}",
        "evidence_ids": [uuid.uuid4()],
        "source_ids": [uuid.uuid4()],
        "content_mode": "verbatim",
    }


def _title(text: str) -> dict:
    return {"component_id": uuid.uuid4(), "component_type": "title", "text": text}


def _evidence(index: int) -> dict:
    return {
        "component_id": uuid.uuid4(),
        "component_type": "evidence_card",
        "heading": f"已验证能力 {index}",
        "body": f"来源明确、权限有效的客户方案事实 {index}，渲染阶段不得改写。",
        "fact_binding": _binding(index),
    }


def _bound_item(index: int) -> dict:
    return {
        "item_id": uuid.uuid4(),
        "label": f"节点 {index}",
        "text": f"经过验证且不可改写的实施事实 {index}",
        "fact_binding": _binding(index),
    }


def _source_item(index: int) -> dict:
    binding = _binding(index)
    binding["content_mode"] = "label_only"
    return {
        "item_id": uuid.uuid4(),
        "label": f"已授权来源资料 {index}",
        "source_id": binding["source_ids"][0],
        "fact_binding": binding,
    }


def _html() -> str:
    layouts = (
        ("cover", [_title("企业数字化转型可信方案")]),
        ("title_body", [_title("方案价值总览"), _evidence(1)]),
        ("two_column", [_title("双路径协同交付"), _evidence(2), _evidence(3)]),
        (
            "three_cards",
            [_title("三项核心能力"), _evidence(4), _evidence(5), _evidence(6)],
        ),
        (
            "evidence_grid",
            [_title("可追溯证据矩阵"), *[_evidence(index) for index in range(7, 11)]],
        ),
        (
            "metric_highlight",
            [
                _title("核心业务指标"),
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "metric",
                    "label": "经验证年度营收",
                    "value": "2025年营业收入为1438亿元",
                    "fact_binding": _binding(11),
                },
            ],
        ),
        (
            "comparison",
            [
                _title("双路径方案对比"),
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "comparison",
                    "heading": "当前基础与目标能力",
                    "left": _bound_item(12),
                    "right": _bound_item(13),
                },
            ],
        ),
        (
            "timeline",
            [
                _title("分阶段实施路线"),
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "timeline",
                    "heading": "从验证到规模化落地",
                    "items": [_bound_item(index) for index in range(14, 18)],
                },
            ],
        ),
        (
            "process",
            [
                _title("可信方案生成流程"),
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "process",
                    "heading": "五步闭环",
                    "steps": [_bound_item(index) for index in range(18, 23)],
                },
            ],
        ),
        (
            "source_list",
            [
                _title("方案事实来源"),
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "source_list",
                    "heading": "当前展示引用的已授权资料",
                    "sources": [_source_item(index) for index in range(23, 29)],
                },
            ],
        ),
    )
    spec = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": token,
                    "components": components,
                }
                for token, components in layouts
            ],
        }
    )
    style = VisualStyleProfileData.model_validate(
        {
            "palette": {
                "primary": "#15385C",
                "secondary": "#2B6E8A",
                "accent": "#D83B32",
                "background": "#F7F9FB",
                "foreground": "#17212B",
            },
            "typography": {
                "heading_font": "Microsoft YaHei",
                "body_font": "Microsoft YaHei",
                "base_size_px": 18,
                "scale_ratio": 1.5,
            },
            "spacing_grid": {
                "base_unit_px": 8,
                "slide_padding_units": 8,
                "component_gap_units": 3,
            },
            "layout_grammar": [token for token, _ in layouts],
        }
    )
    return HTMLRenderer().render(LayoutEngine().position(spec), style)


@pytest.mark.parametrize("viewport", [(1440, 900), (1366, 768), (390, 844)])
def test_supported_html_templates_have_no_browser_overflow_or_blank_slides(
    viewport: tuple[int, int], tmp_path: Path
) -> None:
    executable = _browser_path()
    if executable is None:
        pytest.skip("set PRESENTATION_TEST_BROWSER to run browser visual validation")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.set_content(_html(), wait_until="load")
        slides = page.locator(".slide")
        assert slides.count() == 10
        for index in range(slides.count()):
            slide = slides.nth(index)
            assert slide.inner_text().strip()
            problems = slide.evaluate(
                """slide => {
                    const root = slide.getBoundingClientRect();
                    return [...slide.querySelectorAll('.positioned-component')].flatMap(node => {
                        const box = node.getBoundingClientRect();
                        const outside = box.left < root.left - 1 || box.top < root.top - 1 ||
                          box.right > root.right + 1 || box.bottom > root.bottom + 1;
                        const overflow = node.scrollWidth > node.clientWidth + 1 ||
                          node.scrollHeight > node.clientHeight + 1;
                        return outside || overflow ? [node.dataset.slot] : [];
                    });
                }"""
            )
            assert problems == []
            screenshot = tmp_path / f"{viewport[0]}x{viewport[1]}-slide-{index + 1}.png"
            slide.screenshot(path=str(screenshot))
            assert screenshot.stat().st_size > 5_000
        browser.close()
