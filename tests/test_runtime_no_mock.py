import pytest
from pydantic import ValidationError

from app.api import deps
from app.core.config import Settings, settings
from app.core.errors import AppError, ErrorCode
from app.integrations.presentation import (
    ProviderNotConfiguredError,
    get_presentation_provider,
)
from app.services.intelligence_task_worker import get_intelligence_provider


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ai_mode", "mock"),
        ("feishu_mode", "mock"),
        ("presentation_mode", "mock"),
        ("interactive_html_mode", "mock"),
    ],
)
def test_runtime_configuration_rejects_mock_modes(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_unconfigured_intelligence_provider_never_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(settings, "open_enrich_svc_url", None)

    with pytest.raises(AppError) as captured:
        get_intelligence_provider()

    assert captured.value.code == ErrorCode.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
async def test_unconfigured_presentation_provider_never_renders_mock(monkeypatch) -> None:
    monkeypatch.setattr(settings, "presentation_mode", "live")
    monkeypatch.setattr(settings, "presentation_service_url", None)
    monkeypatch.setattr(settings, "interactive_html_mode", "live")
    monkeypatch.setattr(settings, "interactive_html_api_key", None)
    provider = get_presentation_provider()

    with pytest.raises(ProviderNotConfiguredError):
        await provider.render({"render_mode": "interactive"}, {})


def test_unconfigured_ai_dependency_returns_explicit_503(monkeypatch) -> None:
    deps.get_ai_engine.cache_clear()
    monkeypatch.setattr(
        deps.BailianSettings,
        "from_env",
        classmethod(lambda cls, path: (_ for _ in ()).throw(ValueError("missing"))),
    )
    try:
        with pytest.raises(AppError) as captured:
            deps.get_ai_engine()
    finally:
        deps.get_ai_engine.cache_clear()

    assert captured.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert captured.value.status_code == 503
