import json
import urllib.error

import pytest

from app.ai.model_client import BailianChatClient, BailianSettings, ModelClientError


def test_parse_json_content_accepts_plain_json() -> None:
    parsed = BailianChatClient._parse_json_content('{"name":"测试"}')
    assert parsed == {"name": "测试"}


def test_parse_json_content_accepts_code_fence() -> None:
    parsed = BailianChatClient._parse_json_content('```json\n{"name":"测试"}\n```')
    assert parsed == {"name": "测试"}


def test_parse_json_content_rejects_non_object() -> None:
    with pytest.raises(ModelClientError, match="must be an object"):
        BailianChatClient._parse_json_content("[]")


def test_settings_expose_model_and_parameter_versions() -> None:
    client = BailianChatClient(
        BailianSettings(
            api_key="test-only",
            chat_model="qwen-plus-test",
            temperature=0.2,
        )
    )

    assert client.model_version == "qwen-plus-test"
    assert client.parameter_version == "temperature=0.2;json_object=true"


def test_model_error_carries_stable_retry_information() -> None:
    error = ModelClientError(
        "temporarily unavailable",
        retryable=True,
        status_code=503,
        code="http_error",
    )

    assert error.retryable is True
    assert error.status_code == 503
    assert error.code == "http_error"


class FakeHTTPResponse:
    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode()


def test_chat_client_retries_rate_limit_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(request: object, timeout: float) -> FakeHTTPResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                url="https://example.test",
                code=429,
                msg="rate limited",
                hdrs=None,
                fp=None,
            )
        return FakeHTTPResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = BailianChatClient(
        BailianSettings(
            api_key="test-only",
            max_retries=1,
            retry_backoff_seconds=0,
        )
    )

    result = client._generate_json_sync("system", "user")

    assert result == {"ok": True}
    assert calls == 2
    assert client.last_attempt_count == 2
