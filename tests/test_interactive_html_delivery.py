import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.routes.presentations import open_interactive_html
from app.schemas.presentations import CreateInteractivePresentationRequest
from app.services.presentation_service import PresentationService


@pytest.mark.asyncio
async def test_interactive_html_can_be_created_without_pptx_style_profile() -> None:
    service = PresentationService(AsyncMock(), uuid.uuid4(), uuid.uuid4())
    profile_id = uuid.uuid4()
    expected_run = SimpleNamespace(id=uuid.uuid4())
    service._create_interactive_style_profile = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=profile_id)
    )
    service._create_presentation = AsyncMock(return_value=expected_run)  # type: ignore[method-assign]
    payload = CreateInteractivePresentationRequest(
        audience="客户管理层",
        visual_direction="高留白、瑞士网格、克制的企业蓝",
    )

    result = await service.create_interactive_presentation(uuid.uuid4(), payload)

    assert result is expected_run
    service._create_interactive_style_profile.assert_awaited_once_with(  # type: ignore[attr-defined]
        payload.visual_direction,
        payload.language,
    )
    request = service._create_presentation.await_args.args[1]  # type: ignore[attr-defined]
    assert request.style_profile_id == profile_id
    assert request.output == ["html"]
    assert service._create_presentation.await_args.kwargs["render_mode"] == "interactive"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ready_interactive_html_is_composed_without_server_browser() -> None:
    service = PresentationService(AsyncMock(), uuid.uuid4(), uuid.uuid4())
    service.get_presentation = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "ready"}
    )
    service._latest_artifact = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            html="<!doctype html><html><head></head><body>可信内容</body></html>",
            css="body{color:#172033}",
            render_report={
                "render_mode": "interactive",
                "fact_binding_passed": True,
                "security_passed": True,
                "browser_rendered": False,
            },
        )
    )

    document = await service.get_interactive_html(uuid.uuid4())

    assert "<style>body{color:#172033}</style>" in document
    assert "可信内容" in document


@pytest.mark.asyncio
async def test_artifact_response_forces_browser_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    document = "<!doctype html><html><head></head><body></body></html>"
    monkeypatch.setattr(
        PresentationService,
        "get_interactive_html",
        AsyncMock(return_value=document),
    )

    response = await open_interactive_html(
        uuid.uuid4(),
        AsyncMock(),
        uuid.uuid4(),
        SimpleNamespace(id=uuid.uuid4()),
        download=False,
    )

    assert response.body.decode() == document
    assert response.headers["content-type"].startswith("text/html")
    assert "sandbox allow-scripts" in response.headers["content-security-policy"]
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "private, no-store"
