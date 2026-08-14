import hashlib
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.integrations.feishu import LiveFeishuAdapter
from app.integrations.feishu_events import (
    FeishuEventError,
    parse_text_message_event,
    verify_event_signature,
)
from app.main import create_app


class FakeAsyncClient:
    def __init__(self, responses: dict[str, httpx.Response], **kwargs) -> None:
        del kwargs
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        del kwargs
        return self.responses[url]

    async def post(self, url, **kwargs):
        del kwargs
        return self.responses[url]

    async def request(self, method, url, **kwargs):
        del method, kwargs
        return self.responses[url]


def response(url: str, *, json_body=None, content: bytes | None = None) -> httpx.Response:
    kwargs = {"request": httpx.Request("GET", url)}
    if content is not None:
        kwargs["content"] = content
    else:
        kwargs["json"] = json_body
    return httpx.Response(200, **kwargs)


def test_live_authorization_url_uses_v2_oauth_parameters() -> None:
    adapter = LiveFeishuAdapter(
        "cli_test",
        "secret",
        scopes=["offline_access", "docx:document"],
    )

    parsed = urlparse(adapter.get_authorization_url("state-1", "https://app/callback"))
    query = parse_qs(parsed.query)

    assert query["client_id"] == ["cli_test"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["offline_access docx:document"]
    assert query["state"] == ["state-1"]


@pytest.mark.asyncio
async def test_live_adapter_reads_document_metadata_and_content(monkeypatch) -> None:
    document_id = "doxcni6mOy7jLRWbEylaKKabcef"
    base = "https://open.feishu.cn/open-apis"
    responses = {
        f"{base}/docx/v1/documents/{document_id}": response(
            f"{base}/docx/v1/documents/{document_id}",
            json_body={"code": 0, "data": {"document": {"title": "项目复盘"}}},
        ),
        f"{base}/docx/v1/documents/{document_id}/raw_content": response(
            f"{base}/docx/v1/documents/{document_id}/raw_content",
            json_body={"code": 0, "data": {"content": "真实正文"}},
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )
    adapter = LiveFeishuAdapter("cli_test", "secret")

    document = await adapter.fetch_document(
        f"https://example.feishu.cn/docx/{document_id}", "u-token"
    )

    assert document.title == "项目复盘"
    assert document.content == "真实正文"


def test_feishu_event_signature_and_text_parsing() -> None:
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_expert"}},
            "message": {
                "chat_type": "group",
                "message_type": "text",
                "chat_id": "oc_group",
                "message_id": "om_message",
                "content": json.dumps({"text": "EXP-Q1：建议先做两周试点"}),
            },
        },
    }
    body = json.dumps(payload).encode()
    key = "encrypt-key"
    signature = hashlib.sha256(b"1" + b"nonce" + key.encode() + body).hexdigest()

    verify_event_signature(
        body,
        timestamp="1",
        nonce="nonce",
        signature=signature,
        encrypt_key=key,
    )
    parsed = parse_text_message_event(payload)

    assert parsed == {
        "chat_id": "oc_group",
        "message_id": "om_message",
        "author_id": "ou_expert",
        "text": "EXP-Q1：建议先做两周试点",
    }
    with pytest.raises(FeishuEventError):
        verify_event_signature(
            body,
            timestamp="1",
            nonce="nonce",
            signature="bad",
            encrypt_key=key,
        )


def test_native_feishu_event_url_verification_has_no_business_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "feishu_verification_token", "verification-token")
    response = TestClient(create_app()).post(
        "/api/v1/expert-collaborations/events/feishu",
        json={
            "type": "url_verification",
            "challenge": "challenge-123",
            "token": "verification-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-123"}
