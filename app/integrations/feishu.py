from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.protocols import FeishuDocument, FeishuUserInfo


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

    async def fetch_document(self, url: str) -> FeishuDocument:
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


class LiveFeishuAdapter:
    authorization_endpoint = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    token_endpoint = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    user_info_endpoint = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(self, app_id: str, app_secret: str) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required in live mode")
        self.app_id = app_id
        self.app_secret = app_secret

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "app_id": self.app_id,
                "redirect_uri": redirect_uri,
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
            user_data = payload.get("data", payload)

        feishu_user_id = user_data.get("open_id") or user_data.get("user_id")
        if not feishu_user_id or not user_data.get("name"):
            raise RuntimeError("Feishu user response was missing required fields")
        return FeishuUserInfo(
            feishu_user_id=str(feishu_user_id),
            name=str(user_data["name"]),
            avatar=user_data.get("avatar_url") or user_data.get("avatar_big"),
        )

    async def fetch_document(self, url: str) -> FeishuDocument:
        raise NotImplementedError("Live Feishu document import is implemented in the source module")

