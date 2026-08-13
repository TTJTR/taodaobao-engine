# ruff: noqa: E501
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local" / "chinese-capacity-r1"


def main() -> None:
    html = OUTPUT / "showcase.html"
    report: dict[str, object] = {"viewports": {}, "overflow": [], "missing_titles": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        for name, width, height in (("desktop", 1600, 900), ("laptop", 1440, 900), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.goto(html.resolve().as_uri(), wait_until="networkidle")
            page.screenshot(path=OUTPUT / f"showcase-{name}.png", full_page=True)
            slides = page.locator(".slide")
            report["viewports"][name] = {"width": width, "height": height, "slides": slides.count()}
            if name == "desktop":
                for index in range(slides.count()):
                    slide = slides.nth(index)
                    slide.screenshot(path=OUTPUT / f"slide-{index + 1:02d}.png")
                    problems = slide.locator(".slot").evaluate_all(
                        "els => els.filter(e => e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1).map(e => ({className:e.className, scrollWidth:e.scrollWidth, clientWidth:e.clientWidth, scrollHeight:e.scrollHeight, clientHeight:e.clientHeight}))"
                    )
                    if problems:
                        report["overflow"].append({"slide": index + 1, "slots": problems})
                    if slide.locator(".title").count() != 1:
                        report["missing_titles"].append(index + 1)
            page.close()
        browser.close()
    report["passed"] = not report["overflow"] and not report["missing_titles"]
    (OUTPUT / "browser-validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
