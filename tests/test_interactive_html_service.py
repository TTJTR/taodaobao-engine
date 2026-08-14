import pytest

from app.integrations.presentation import MockPresentationProvider, RoutingPresentationProvider
from app.services.interactive_html_service import (
    InteractiveHTMLValidationError,
    MockInteractiveHTMLProvider,
    audit_interactive_html,
)


@pytest.fixture
def snapshot() -> dict:
    return {
        "title": "淘到宝可信方案",
        "render_mode": "interactive",
        "released_claims": [
            {
                "claim_id": "claim-1",
                "text": "已完成门店视觉质检试点。",
                "boundary": "historical_fact",
            },
            {
                "claim_id": "claim-2",
                "text": "支持私有化部署。",
                "boundary": "enterprise_capability",
            },
        ],
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "quote": "项目已通过客户验收。",
            }
        ],
    }


def test_audit_accepts_exact_bound_claims_and_safe_interaction(snapshot: dict) -> None:
    document = """<!doctype html><html><head><title data-document-title>淘到宝可信方案</title>
<style>nav{position:sticky;top:0}</style></head><body>
<nav data-role="section-nav"><a href="#facts" data-system-label>可信事实</a></nav>
<section id="facts"><p data-claim-id="claim-1">已完成门店视觉质检试点。</p>
<p data-claim-id="claim-2">支持私有化部署。</p>
<blockquote data-evidence-id="evidence-1">项目已通过客户验收。</blockquote></section>
<script>document.querySelector('a').addEventListener('click',event=>{
event.preventDefault();document.querySelector('#facts').classList.toggle('active')})</script>
</body></html>"""
    report = audit_interactive_html(document, snapshot)
    assert report["fact_binding_passed"] is True
    assert report["security_passed"] is True
    assert report["claim_count"] == 2


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            """<!doctype html><html><head></head><body>
<nav data-role="section-nav"><span data-system-label>可信事实</span></nav>
<p data-claim-id="claim-1">效率显著改善。</p>
<p data-claim-id="claim-2">支持私有化部署。</p></body></html>""",
            "未逐字复制",
        ),
        (
            """<!doctype html><html><head></head><body>
<nav data-role="section-nav"><span data-system-label>可信事实</span></nav>
<p data-claim-id="claim-1">已完成门店视觉质检试点。</p>
<p data-claim-id="claim-2">支持私有化部署。</p>
<p>预计节省 30% 成本</p></body></html>""",
            "未绑定可见文字",
        ),
        (
            """<!doctype html><html><head></head><body>
<nav data-role="section-nav"><span data-system-label>可信事实</span></nav>
<p data-claim-id="claim-1">已完成门店视觉质检试点。</p>
<p data-claim-id="claim-2">支持私有化部署。</p>
<script>fetch('/api/private')</script></body></html>""",
            "JavaScript",
        ),
        (
            """<!doctype html><html><head><style>.x{background:url(https://x)}</style></head>
<body><nav data-role="section-nav"><span data-system-label>可信事实</span></nav>
<p data-claim-id="claim-1">已完成门店视觉质检试点。</p>
<p data-claim-id="claim-2">支持私有化部署。</p></body></html>""",
            "CSS",
        ),
    ],
)
def test_audit_blocks_hallucination_and_browser_escape(
    snapshot: dict, document: str, message: str
) -> None:
    with pytest.raises(InteractiveHTMLValidationError, match=message):
        audit_interactive_html(document, snapshot)


@pytest.mark.asyncio
async def test_routing_provider_uses_interactive_mode_without_replacing_standard(
    snapshot: dict,
) -> None:
    provider = RoutingPresentationProvider(
        MockPresentationProvider(), MockInteractiveHTMLProvider()
    )
    interactive = await provider.render(snapshot, {})
    standard = await provider.render(
        {
            "title": "标准演示",
            "released_claims": [
                {"claim_id": "claim-1", "text": "原文", "boundary": "historical_fact"}
            ],
        },
        {},
    )
    assert interactive["provider_mode"] == "interactive-mock"
    assert interactive["render_report"]["fact_binding_passed"] is True
    assert interactive["render_report"]["passed"] is False
    assert standard["provider_mode"] == "mock"
    assert "<script>" not in standard["html"]
