import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.protocols import (
    FeishuCreatedDocument,
    FeishuCreatedGroup,
    FeishuDocument,
    FeishuResource,
    FeishuTokenInfo,
    FeishuUserInfo,
)


class MockFeishuAdapter:
    def __init__(self, public_base_url: str) -> None:
        self.public_base_url = public_base_url.rstrip("/")

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"code": "mock-code", "state": state})
        return f"{redirect_uri}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> FeishuUserInfo:
        del redirect_uri
        if code != "mock-code":
            raise ValueError("invalid mock authorization code")
        return FeishuUserInfo(
            feishu_user_id="mock_user_001",
            name="演示售前顾问",
            avatar=None,
        )

    async def refresh_access_token(self, refresh_token: str) -> FeishuTokenInfo:
        del refresh_token
        return FeishuTokenInfo(
            access_token="mock-access-token",
            refresh_token="mock-refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )

    async def fetch_document(self, url: str, access_token: str | None = None) -> FeishuDocument:
        del access_token
        return FeishuDocument(
            title="A 客户智能质检项目会议纪要",
            content=(
                "# 客户背景\n"
                "A 客户是一家快消零售企业。\n\n"
                "# 当前问题\n"
                "客户希望两周内上线不依赖专用硬件的门店质检方案。\n\n"
                "# 约束\n"
                "现有摄像头需要复用，试点阶段预算有限，结果需要人工复核。"
            ),
            author="演示方案团队",
            source_url=url,
            source_updated_at="2026-08-06T00:00:00Z",
        )

    async def create_document(
        self, title: str, content: dict[str, Any], access_token: str | None
    ) -> FeishuCreatedDocument:
        del content, access_token
        document_id = f"mock-doc-{uuid.uuid4()}"
        return FeishuCreatedDocument(
            document_id=document_id,
            url=f"{self.public_base_url}/mock/feishu/documents/{document_id}",
        )

    async def list_resources(
        self,
        resource_type: str,
        access_token: str | None,
        page_token: str | None = None,
    ) -> tuple[list[FeishuResource], str | None]:
        del access_token, page_token
        token = "mock-minute-001" if resource_type == "minute" else "mock-doc-001"
        url_kind = "minutes" if resource_type == "minute" else "docx"
        return (
            [
                FeishuResource(
                    token=token,
                    title="模拟妙记" if resource_type == "minute" else "模拟飞书文档",
                    resource_type=resource_type,
                    url=f"https://example.feishu.cn/{url_kind}/{token}",
                    owner_name="演示售前顾问",
                    updated_at=datetime.now(UTC),
                )
            ],
            None,
        )

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> FeishuCreatedGroup:
        del name, member_ids, access_token, idempotency_key
        return FeishuCreatedGroup(chat_id=f"mock-chat-{uuid.uuid4()}")

    async def send_group_message(
        self,
        chat_id: str,
        content: dict[str, Any],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> str:
        del chat_id, content, access_token, idempotency_key
        return f"mock-message-{uuid.uuid4()}"


class LiveFeishuAdapter:
    authorization_endpoint = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    token_endpoint = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    user_info_endpoint = "https://open.feishu.cn/open-apis/authen/v1/user_info"
    api_base = "https://open.feishu.cn/open-apis"

    tenant_token_endpoint = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

    def __init__(
        self, app_id: str, app_secret: str, *, scopes: list[str] | None = None
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required in live mode")
        self.app_id = app_id
        self.app_secret = app_secret
        self.scopes = list(dict.fromkeys(scopes or ["offline_access"]))
        self._tenant_access_token: str | None = None
        self._tenant_token_expires_at: datetime | None = None

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self.app_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
            }
        )
        return f"{self.authorization_endpoint}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> FeishuUserInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                self.token_endpoint,
                json={
                    "grant_type": "authorization_code",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            token_response.raise_for_status()
            token_payload: dict[str, Any] = token_response.json()
            self._raise_oauth_error(token_payload)
            access_token = token_payload.get("access_token") or token_payload.get("data", {}).get(
                "access_token"
            )
            if not access_token:
                raise RuntimeError("Feishu token response did not include access_token")

            user_response = await client.get(
                self.user_info_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            payload: dict[str, Any] = user_response.json()
            self._raise_api_error(payload)
            user_data = payload.get("data", payload)

        feishu_user_id = user_data.get("open_id") or user_data.get("user_id")
        if not feishu_user_id or not user_data.get("name"):
            raise RuntimeError("Feishu user response was missing required fields")
        return FeishuUserInfo(
            feishu_user_id=str(feishu_user_id),
            name=str(user_data["name"]),
            avatar=user_data.get("avatar_url") or user_data.get("avatar_big"),
            tenant_key=user_data.get("tenant_key"),
            access_token=str(access_token),
            refresh_token=token_payload.get("refresh_token")
            or token_payload.get("data", {}).get("refresh_token"),
            expires_at=datetime.now(UTC)
            + timedelta(
                seconds=int(
                    token_payload.get("expires_in")
                    or token_payload.get("data", {}).get("expires_in")
                    or 7200
                )
            ),
        )

    async def refresh_access_token(self, refresh_token: str) -> FeishuTokenInfo:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.token_endpoint,
                json={
                    "grant_type": "refresh_token",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            self._raise_oauth_error(payload)
        data = payload.get("data", payload)
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError("Feishu refresh response omitted access_token")
        return FeishuTokenInfo(
            access_token=str(access_token),
            refresh_token=data.get("refresh_token") or refresh_token,
            expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in") or 7200)),
        )

    async def fetch_document(self, url: str, access_token: str | None = None) -> FeishuDocument:
        token = self._require_access_token(access_token)
        minute_id = self._minute_id(url)
        if minute_id:
            return await self._fetch_minute(url, minute_id, token)
        document_id = self._document_id(url)
        metadata_payload = await self._request(
            "GET",
            f"/docx/v1/documents/{document_id}",
            token,
        )
        content_payload = await self._request(
            "GET",
            f"/docx/v1/documents/{document_id}/raw_content",
            token,
        )
        document = metadata_payload.get("data", {}).get("document", {})
        data = content_payload.get("data", {})
        return FeishuDocument(
            title=str(document.get("title") or f"Feishu document {document_id}"),
            content=str(data.get("content") or ""),
            author="飞书文档",
            source_url=url,
            source_updated_at=None,
        )

    async def list_resources(
        self,
        resource_type: str,
        access_token: str | None,
        page_token: str | None = None,
    ) -> tuple[list[FeishuResource], str | None]:
        token = self._require_access_token(access_token)
        if resource_type == "minute":
            request_params = {"page_size": "30"}
            if page_token:
                request_params["page_token"] = page_token
            payload = await self._request(
                "POST",
                "/minutes/v1/minutes/search",
                token,
                json_body={"query": ""},
                params=request_params,
            )
            data = payload.get("data", {})
            raw_items = data.get("minute_list") or data.get("items") or []
            items = [
                FeishuResource(
                    token=str(item.get("minute_token") or item.get("token") or ""),
                    title=str(item.get("title") or "未命名妙记"),
                    resource_type="minute",
                    url=str(
                        item.get("url")
                        or "https://feishu.cn/minutes/"
                        f"{item.get('minute_token') or item.get('token')}"
                    ),
                    owner_name=item.get("owner_name"),
                    updated_at=self._optional_datetime(item.get("update_time")),
                )
                for item in raw_items
                if item.get("minute_token") or item.get("token")
            ]
        elif resource_type == "document":
            request_params = {"page_size": "50"}
            if page_token:
                request_params["page_token"] = page_token
            payload = await self._request("GET", "/drive/v1/files", token, params=request_params)
            data = payload.get("data", {})
            raw_items = data.get("files") or data.get("items") or []
            items = [
                FeishuResource(
                    token=str(item.get("token") or ""),
                    title=str(item.get("name") or item.get("title") or "未命名文档"),
                    resource_type="document",
                    url=str(item.get("url") or f"https://feishu.cn/docx/{item.get('token')}"),
                    owner_name=item.get("owner_name"),
                    updated_at=self._optional_datetime(item.get("modified_time")),
                )
                for item in raw_items
                if item.get("token") and item.get("type") in {None, "docx", "doc"}
            ]
        else:
            raise ValueError("resource_type must be document or minute")
        next_page_token = data.get("page_token") if data.get("has_more") else None
        return items, data.get("next_page_token") or next_page_token

    async def create_document(
        self, title: str, content: dict[str, Any], access_token: str | None
    ) -> FeishuCreatedDocument:
        token = self._require_access_token(access_token)
        created = await self._request(
            "POST", "/docx/v1/documents", token, json_body={"title": title}
        )
        document = created.get("data", {}).get("document", {})
        document_id = str(document.get("document_id") or "")
        if not document_id:
            raise RuntimeError("Feishu create document response omitted document_id")
        blocks = self._document_blocks(content)
        if blocks:
            await self._request(
                "POST",
                f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                token,
                json_body={"children": blocks, "index": -1},
            )
        return FeishuCreatedDocument(
            document_id=document_id,
            url=f"https://feishu.cn/docx/{document_id}",
        )

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> FeishuCreatedGroup:
        del access_token
        token = await self._get_tenant_access_token()
        payload = await self._request(
            "POST",
            "/im/v1/chats",
            token,
            json_body={
                "name": name,
                "user_id_list": member_ids,
                "chat_mode": "group",
                "chat_type": "private",
                **({"uuid": idempotency_key} if idempotency_key else {}),
            },
            params={"user_id_type": "open_id"},
        )
        chat_id = str(payload.get("data", {}).get("chat_id") or "")
        if not chat_id:
            raise RuntimeError("Feishu create group response omitted chat_id")
        return FeishuCreatedGroup(chat_id=chat_id)

    async def send_group_message(
        self,
        chat_id: str,
        content: dict[str, Any],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> str:
        del access_token
        token = await self._get_tenant_access_token()
        is_text = set(content) == {"text"}
        payload = await self._request(
            "POST",
            "/im/v1/messages",
            token,
            json_body={
                "receive_id": chat_id,
                "msg_type": "text" if is_text else "interactive",
                "content": json.dumps(content, ensure_ascii=False),
                **({"uuid": idempotency_key} if idempotency_key else {}),
            },
            params={"receive_id_type": "chat_id"},
        )
        return str(payload.get("data", {}).get("message_id") or "")

    async def _get_tenant_access_token(self) -> str:
        now = datetime.now(UTC)
        if (
            self._tenant_access_token
            and self._tenant_token_expires_at
            and self._tenant_token_expires_at > now + timedelta(seconds=60)
        ):
            return self._tenant_access_token
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.tenant_token_endpoint,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        self._raise_api_error(payload)
        token = payload.get("tenant_access_token")
        if not token:
            raise RuntimeError("Feishu tenant token response omitted tenant_access_token")
        self._tenant_access_token = str(token)
        self._tenant_token_expires_at = now + timedelta(seconds=int(payload.get("expire") or 7200))
        return self._tenant_access_token

    async def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(3):
                response = await client.request(
                    method,
                    f"{self.api_base}{path}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "X-Request-ID": request_id,
                    },
                    json=json_body,
                    params=params,
                )
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        if payload.get("code", 0) != 0:
            raise RuntimeError(f"Feishu API failed: {payload.get('code')} {payload.get('msg', '')}")
        return payload

    @staticmethod
    def _require_access_token(access_token: str | None) -> str:
        if not access_token:
            raise RuntimeError("Feishu user access token is unavailable; re-authorize first")
        return access_token

    @staticmethod
    def _document_id(url: str) -> str:
        match = re.search(r"/(?:docx|docs)/([A-Za-z0-9_-]+)", url)
        if not match:
            raise ValueError("unsupported Feishu document URL")
        return match.group(1)

    @staticmethod
    def _minute_id(url: str) -> str | None:
        match = re.search(r"/(?:minutes|minutedetail)/([A-Za-z0-9_-]+)", url)
        return match.group(1) if match else None

    async def _fetch_minute(self, url: str, minute_id: str, access_token: str) -> FeishuDocument:
        info = await self._request("GET", f"/minutes/v1/minutes/{minute_id}", access_token)
        data = info.get("data", {}).get("minute", info.get("data", {}))
        content = await self._request_text(
            f"/minutes/v1/minutes/{minute_id}/transcript",
            access_token,
            params={"file_format": "txt", "need_speaker": "true"},
        )
        return FeishuDocument(
            title=str(data.get("title") or "未命名妙记"),
            content=str(content),
            author=str(data.get("owner_name") or "Feishu Minutes"),
            source_url=url,
            source_updated_at=data.get("update_time"),
        )

    async def _request_text(
        self, path: str, access_token: str, *, params: dict[str, str] | None = None
    ) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.api_base}{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
        response.raise_for_status()
        return response.content.decode("utf-8-sig")

    @staticmethod
    def _raise_oauth_error(payload: dict[str, Any]) -> None:
        code = payload.get("code", 0)
        if code not in {0, "0", None} or payload.get("error"):
            detail = payload.get("error_description") or payload.get("msg") or payload.get("error")
            raise RuntimeError(f"Feishu OAuth failed: {code} {detail or ''}".strip())

    @staticmethod
    def _raise_api_error(payload: dict[str, Any]) -> None:
        if payload.get("code", 0) not in {0, "0", None}:
            raise RuntimeError(f"Feishu API failed: {payload.get('code')} {payload.get('msg', '')}")

    @staticmethod
    def _optional_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, int | float):
            divisor = 1000 if value > 10_000_000_000 else 1
            return datetime.fromtimestamp(value / divisor, tz=UTC)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _document_blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for section in content.get("sections", []):
            title = str(section.get("title") or "").strip()
            if title:
                blocks.append(
                    {
                        "block_type": 3,
                        "heading1": {"elements": [{"text_run": {"content": title}}]},
                    }
                )
            for item in section.get("items", []):
                text_value = str(item.get("text") or "").strip()
                if text_value:
                    blocks.append(
                        {
                            "block_type": 2,
                            "text": {"elements": [{"text_run": {"content": text_value}}]},
                        }
                    )
        return blocks
