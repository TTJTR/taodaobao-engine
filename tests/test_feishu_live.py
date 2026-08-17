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
        f"{base}/drive/v1/permissions/{document_id}/members": response(
            f"{base}/drive/v1/permissions/{document_id}/members",
            json_body={"code": 0, "data": {"items": []}},
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


@pytest.mark.asyncio
async def test_live_adapter_resolves_wiki_node_before_reading_document(monkeypatch) -> None:
    node_token = "A0I3whrOQi4Jo7kCmGwclVk1nce"
    document_id = "doxcnResolvedDocumentToken"
    base = "https://open.feishu.cn/open-apis"
    responses = {
        f"{base}/wiki/v2/spaces/get_node": response(
            f"{base}/wiki/v2/spaces/get_node",
            json_body={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": node_token,
                        "obj_type": "docx",
                        "obj_token": document_id,
                    }
                },
            },
        ),
        f"{base}/docx/v1/documents/{document_id}": response(
            f"{base}/docx/v1/documents/{document_id}",
            json_body={"code": 0, "data": {"document": {"title": "Wiki document"}}},
        ),
        f"{base}/docx/v1/documents/{document_id}/raw_content": response(
            f"{base}/docx/v1/documents/{document_id}/raw_content",
            json_body={"code": 0, "data": {"content": "Wiki content"}},
        ),
        f"{base}/drive/v1/permissions/{document_id}/members": response(
            f"{base}/drive/v1/permissions/{document_id}/members",
            json_body={"code": 0, "data": {"items": []}},
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )
    adapter = LiveFeishuAdapter("cli_test", "secret")

    document = await adapter.fetch_document(
        f"https://larkcommunity.feishu.cn/wiki/{node_token}", "u-token"
    )

    assert document.title == "Wiki document"
    assert document.content == "Wiki content"
    assert document.source_url.endswith(f"/wiki/{node_token}")


@pytest.mark.asyncio
async def test_live_adapter_returns_document_owner_and_collaborators(monkeypatch) -> None:
    document_id = "doxcnCollaborators"
    base = "https://open.feishu.cn/open-apis"
    responses = {
        f"{base}/docx/v1/documents/{document_id}": response(
            f"{base}/docx/v1/documents/{document_id}",
            json_body={"code": 0, "data": {"document": {"title": "客户项目复盘"}}},
        ),
        f"{base}/docx/v1/documents/{document_id}/raw_content": response(
            f"{base}/docx/v1/documents/{document_id}/raw_content",
            json_body={"code": 0, "data": {"content": "带来源的原文"}},
        ),
        f"{base}/drive/v1/permissions/{document_id}/members": response(
            f"{base}/drive/v1/permissions/{document_id}/members",
            json_body={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "member_type": "openid",
                            "member_id": "ou_owner",
                            "name": "文档作者",
                            "perm": "full_access",
                        },
                        {
                            "member_type": "openid",
                            "member_id": "ou_editor",
                            "name": "方案协作者",
                            "perm": "edit",
                        },
                    ]
                },
            },
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )

    document = await LiveFeishuAdapter("cli_test", "secret").fetch_document(
        f"https://example.feishu.cn/docx/{document_id}", "u-token"
    )

    assert document.author == "文档作者"
    assert [item.name for item in document.collaborators] == ["文档作者", "方案协作者"]
    assert document.collaborators[0].is_owner is True


@pytest.mark.asyncio
async def test_live_adapter_keeps_collaborators_when_names_are_restricted(monkeypatch) -> None:
    document_id = "doxcnRestrictedNames"
    base = "https://open.feishu.cn/open-apis"
    responses = {
        f"{base}/docx/v1/documents/{document_id}": response(
            f"{base}/docx/v1/documents/{document_id}",
            json_body={"code": 0, "data": {"document": {"title": "客户画像"}}},
        ),
        f"{base}/docx/v1/documents/{document_id}/raw_content": response(
            f"{base}/docx/v1/documents/{document_id}/raw_content",
            json_body={"code": 0, "data": {"content": "有来源的原文"}},
        ),
        f"{base}/drive/v1/permissions/{document_id}/members": response(
            f"{base}/drive/v1/permissions/{document_id}/members",
            json_body={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "member_type": "openid",
                            "member_id": "ou_current",
                            "perm": "full_access",
                        },
                        {
                            "member_type": "openid",
                            "member_id": "ou_other",
                            "perm": "full_access",
                        },
                    ]
                },
            },
        ),
        f"{base}/authen/v1/user_info": response(
            f"{base}/authen/v1/user_info",
            json_body={
                "code": 0,
                "data": {"open_id": "ou_current", "name": "当前用户"},
            },
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )

    document = await LiveFeishuAdapter("cli_test", "secret").fetch_document(
        f"https://example.feishu.cn/docx/{document_id}", "u-token"
    )

    assert document.author == "当前用户（当前授权用户）、文档协作者1（姓名受限）"
    assert [item.feishu_user_id for item in document.collaborators] == [
        "ou_current",
        "ou_other",
    ]
    assert [item.name for item in document.collaborators] == [
        "当前用户（当前授权用户）",
        "文档协作者1（姓名受限）",
    ]


@pytest.mark.asyncio
async def test_live_adapter_creates_and_appends_real_bitable_records(monkeypatch) -> None:
    base = "https://open.feishu.cn/open-apis"
    app_token = "bascnRealApp"
    table_id = "tblRealTable"
    responses = {
        f"{base}/bitable/v1/apps": response(
            f"{base}/bitable/v1/apps",
            json_body={
                "code": 0,
                "data": {
                    "app": {
                        "app_token": app_token,
                        "url": f"https://example.feishu.cn/base/{app_token}",
                    }
                },
            },
        ),
        f"{base}/bitable/v1/apps/{app_token}/tables": response(
            f"{base}/bitable/v1/apps/{app_token}/tables",
            json_body={"code": 0, "data": {"table": {"table_id": table_id}}},
        ),
        f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create": response(
            f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            json_body={"code": 0, "data": {"records": [{"record_id": "rec1"}]}},
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )
    adapter = LiveFeishuAdapter("cli_test", "secret")

    target = await adapter.create_bitable("淘到宝·今日战情", "u-token")
    count = await adapter.append_bitable_records(
        target.app_token,
        target.table_id,
        [{"主题": "东岳智行", "类型": "客户画像"}],
        "u-token",
    )

    assert target.app_token == app_token
    assert target.table_id == table_id
    assert count == 1


@pytest.mark.asyncio
async def test_live_adapter_accepts_current_top_level_table_id(monkeypatch) -> None:
    base = "https://open.feishu.cn/open-apis"
    app_token = "bascnCurrentApp"
    table_id = "tblCurrentTable"
    responses = {
        f"{base}/bitable/v1/apps": response(
            f"{base}/bitable/v1/apps",
            json_body={
                "code": 0,
                "data": {
                    "app": {
                        "app_token": app_token,
                        "url": f"https://feishu.cn/base/{app_token}",
                    }
                },
            },
        ),
        f"{base}/bitable/v1/apps/{app_token}/tables": response(
            f"{base}/bitable/v1/apps/{app_token}/tables",
            json_body={"code": 0, "data": {"table_id": table_id}},
        ),
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )

    target = await LiveFeishuAdapter("cli_test", "secret").create_bitable(
        "淘到宝·今日战情", "u-token"
    )

    assert target.table_id == table_id


@pytest.mark.asyncio
async def test_live_adapter_rejects_non_document_wiki_nodes(monkeypatch) -> None:
    node_token = "WikiSheetNode"
    base = "https://open.feishu.cn/open-apis"
    responses = {
        f"{base}/wiki/v2/spaces/get_node": response(
            f"{base}/wiki/v2/spaces/get_node",
            json_body={
                "code": 0,
                "data": {
                    "node": {
                        "node_token": node_token,
                        "obj_type": "sheet",
                        "obj_token": "shtcnUnsupported",
                    }
                },
            },
        )
    }
    monkeypatch.setattr(
        "app.integrations.feishu.httpx.AsyncClient",
        lambda **kwargs: FakeAsyncClient(responses, **kwargs),
    )
    adapter = LiveFeishuAdapter("cli_test", "secret")

    with pytest.raises(ValueError, match="unsupported Feishu wiki object type: sheet"):
        await adapter.fetch_document(
            f"https://example.feishu.cn/wiki/{node_token}", "u-token"
        )


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
