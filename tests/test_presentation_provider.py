import pytest

from app.integrations.presentation import MockPresentationProvider


@pytest.mark.asyncio
async def test_mock_reference_provider_only_accepts_explicit_synthetic_demo() -> None:
    provider = MockPresentationProvider()
    unsafe = await provider.parse_reference({"storage_key": "uploads/customer.pptx"})
    demo = await provider.parse_reference({"storage_key": "demo://reference.pptx"})
    assert unsafe["security_report"]["safe"] is False
    assert demo["security_report"]["safe"] is True
    assert demo["provider_mode"] == "mock"


@pytest.mark.asyncio
async def test_mock_renderer_escapes_claim_text_and_never_emits_scripts() -> None:
    result = await MockPresentationProvider().render(
        {
            "title": "演示",
            "released_claims": [
                {
                    "claim_id": "claim-1",
                    "text": '<script>alert("x")</script>',
                    "boundary": "historical_fact",
                }
            ],
        },
        {},
    )
    assert "<script>" not in result["html"]
    assert "&lt;script&gt;" in result["html"]
    assert result["render_report"]["passed"] is False
    assert result["status"] == "needs_review"


@pytest.mark.asyncio
async def test_export_retry_uses_existing_html_artifact_without_rendering() -> None:
    provider = MockPresentationProvider()
    result = await provider.export(
        {"artifact_id": "artifact-1", "html": "<main></main>", "css": ""}, "html"
    )
    assert result["object_key"] == "db://html-artifacts/artifact-1"
    with pytest.raises(RuntimeError, match="does not produce PDF"):
        await provider.export({"artifact_id": "artifact-1"}, "pdf")
