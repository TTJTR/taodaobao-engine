from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FeishuUserInfo:
    feishu_user_id: str
    name: str
    avatar: str | None = None


@dataclass(frozen=True, slots=True)
class FeishuDocument:
    title: str
    content: str
    author: str
    source_url: str
    source_updated_at: str | None = None


@runtime_checkable
class FeishuAdapter(Protocol):
    def get_authorization_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(self, code: str, redirect_uri: str) -> FeishuUserInfo: ...

    async def fetch_document(self, url: str) -> FeishuDocument: ...


@runtime_checkable
class LLMAdapter(Protocol):
    async def complete_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]: ...

