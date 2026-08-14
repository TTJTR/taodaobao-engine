import html
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.services.interactive_html_service import (
    LiveInteractiveHTMLProvider,
    MockInteractiveHTMLProvider,
)


class PresentationProvider(Protocol):
    mode: str

    async def parse_reference(self, reference: dict[str, Any]) -> dict[str, Any]: ...

    async def generate_style(self, references: list[dict[str, Any]]) -> dict[str, Any]: ...

    async def render(
        self, input_snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def export(self, artifact: dict[str, Any], export_type: str) -> dict[str, Any]: ...


class MockPresentationProvider:
    """Schema-compatible deterministic provider; every result is explicitly labelled mock."""

    mode = "mock"

    async def parse_reference(self, reference: dict[str, Any]) -> dict[str, Any]:
        synthetic_demo = str(reference.get("storage_key", "")).startswith("demo://")
        return {
            "provider_mode": self.mode,
            "page_count": 0,
            "style_signals": {},
            "narrative_signals": {},
            "failed_pages": [],
            "security_report": {
                "scanned": synthetic_demo,
                "safe": synthetic_demo,
                "reason": (
                    "synthetic demo reference accepted without external bytes"
                    if synthetic_demo
                    else "mock provider does not inspect PPTX bytes"
                ),
            },
        }

    async def generate_style(self, references: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "provider_mode": self.mode,
            "visual_json": {
                "palette": {"primary": "#173F8A", "accent": "#FF8A3D"},
                "typography": {"title": "sans-serif", "body": "sans-serif"},
                "layout_grammar": ["top_title", "three_cards"],
                "density": "medium",
            },
            "narrative_json": {
                "language": "zh-CN",
                "structure": "conclusion_first",
                "fact_copying_from_reference": False,
            },
            "conflict_notes": [
                "当前为 Mock 风格画像，尚未由真实 PPTX 解析服务确认，不能跨任务复用。"
            ],
        }

    async def render(
        self, input_snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]:
        claims = input_snapshot.get("released_claims", [])
        blocks = [
            {
                "block_id": f"block-{index}",
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "boundary": claim["boundary"],
                "fact_block": claim["boundary"] in {"historical_fact", "enterprise_capability"},
                "locked": False,
            }
            for index, claim in enumerate(claims, start=1)
        ]
        spec = input_snapshot.get("spec_override") or {
            "schema_version": "presentation-spec-v1",
            "pages": [{"page_id": "page-1", "purpose": "可信方案摘要", "blocks": blocks}],
            "source_claim_ids": [item["claim_id"] for item in claims],
            "provider_mode": self.mode,
        }
        spec_blocks = [block for page in spec.get("pages", []) for block in page.get("blocks", [])]
        block_html = "".join(
            f'<article class="card" data-claim-id="{html.escape(item["claim_id"])}">'
            f"<p>{html.escape(item['text'])}</p>"
            f"<small>{html.escape(item['boundary'])}</small></article>"
            for item in spec_blocks
        )
        document = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="stylesheet" href="style.css"></head><body><main class="slide">'
            f"<h1>{html.escape(input_snapshot.get('title', '淘到宝方案'))}</h1>"
            f"{block_html}</main></body></html>"
        )
        css = (
            ":root{--primary:#173F8A;--accent:#FF8A3D}*{box-sizing:border-box}"
            "body{margin:0;font-family:sans-serif;color:#172033}.slide{width:1280px;min-height:720px;"
            "padding:64px;background:#fff}.card{border-left:5px solid var(--accent);padding:16px;"
            "margin:12px 0;background:#f5f7fb}small{color:#667085}"
            "@media print{.slide{page-break-after:always}}"
        )
        return {
            "provider_mode": self.mode,
            "spec": spec,
            "html": document,
            "css": css,
            "assets": [],
            "render_report": {
                "browser_rendered": False,
                "print_checked": False,
                "passed": False,
                "reason": "mock provider does not run Chromium visual QA",
            },
            "status": "needs_review",
        }

    async def export(self, artifact: dict[str, Any], export_type: str) -> dict[str, Any]:
        if export_type != "html":
            raise RuntimeError("mock presentation provider does not produce PDF")
        return {
            "provider_mode": self.mode,
            "object_key": f"db://html-artifacts/{artifact['artifact_id']}",
        }


class LivePresentationProvider:
    mode = "live"

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}{path}", json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("presentation provider returned a non-object response")
        return result

    async def parse_reference(self, reference: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1/references/parse", reference)

    async def generate_style(self, references: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._post("/v1/styles/generate", {"references": references})

    async def render(
        self, input_snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._post(
            "/v1/presentations/render",
            {"input_snapshot": input_snapshot, "style_profile": style_profile},
        )

    async def export(self, artifact: dict[str, Any], export_type: str) -> dict[str, Any]:
        return await self._post(
            "/v1/presentations/export", {"artifact": artifact, "export_type": export_type}
        )


class RoutingPresentationProvider:
    """Keep the deterministic provider intact and isolate AI-authored interactive HTML."""

    def __init__(self, base: PresentationProvider, interactive) -> None:
        self.base = base
        self.interactive = interactive
        self.mode = base.mode

    async def parse_reference(self, reference: dict[str, Any]) -> dict[str, Any]:
        return await self.base.parse_reference(reference)

    async def generate_style(self, references: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.base.generate_style(references)

    async def render(
        self, input_snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]:
        if input_snapshot.get("render_mode") == "interactive":
            return await self.interactive.render(input_snapshot, style_profile)
        return await self.base.render(input_snapshot, style_profile)

    async def export(self, artifact: dict[str, Any], export_type: str) -> dict[str, Any]:
        return await self.base.export(artifact, export_type)


def get_presentation_provider() -> PresentationProvider:
    if settings.presentation_mode == "mock":
        base: PresentationProvider = MockPresentationProvider()
    else:
        if not settings.presentation_service_url:
            raise RuntimeError("APP_PRESENTATION_SERVICE_URL is required in live mode")
        base = LivePresentationProvider(
            settings.presentation_service_url, settings.presentation_service_api_key
        )
    if settings.interactive_html_mode == "live":
        if not settings.interactive_html_api_key:
            raise RuntimeError("APP_INTERACTIVE_HTML_API_KEY is required in live mode")
        interactive = LiveInteractiveHTMLProvider(
            settings.interactive_html_base_url,
            settings.interactive_html_api_key,
            settings.interactive_html_model,
            settings.interactive_html_timeout_seconds,
        )
    else:
        interactive = MockInteractiveHTMLProvider()
    return RoutingPresentationProvider(base, interactive)
