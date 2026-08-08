from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FeishuUserInfo:
    feishu_user_id: str
    name: str
    avatar: str | None = None
    tenant_key: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeishuDocument:
    title: str
    content: str
    author: str
    source_url: str
    source_updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuCreatedDocument:
    document_id: str
    url: str


@dataclass(frozen=True, slots=True)
class FeishuCreatedGroup:
    chat_id: str


@dataclass(frozen=True, slots=True)
class FeishuResource:
    token: str
    title: str
    resource_type: str
    url: str
    owner_name: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeishuTokenInfo:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@runtime_checkable
class FeishuAdapter(Protocol):
    def get_authorization_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(self, code: str, redirect_uri: str) -> FeishuUserInfo: ...

    async def refresh_access_token(self, refresh_token: str) -> FeishuTokenInfo: ...

    async def fetch_document(self, url: str, access_token: str | None = None) -> FeishuDocument: ...

    async def list_resources(
        self,
        resource_type: str,
        access_token: str | None,
        page_token: str | None = None,
    ) -> tuple[list[FeishuResource], str | None]: ...

    async def create_document(
        self, title: str, content: dict[str, Any], access_token: str | None
    ) -> FeishuCreatedDocument: ...

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> FeishuCreatedGroup: ...

    async def send_group_message(
        self,
        chat_id: str,
        content: dict[str, Any],
        access_token: str | None,
        idempotency_key: str | None = None,
    ) -> str: ...


@runtime_checkable
class LLMAdapter(Protocol):
    async def complete_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]: ...
