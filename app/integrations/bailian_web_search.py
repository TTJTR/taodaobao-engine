from __future__ import annotations

import uuid
from time import perf_counter
from typing import Any

import httpx

from app.core.errors import AppError, ErrorCode
from app.schemas.search_provider import SearchDiscoveryResult, SearchSource

BAILIAN_NATIVE_SEARCH_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
)


class BailianWebSearchAdapter:
    """Real Alibaba Model Studio web search used only for source discovery."""

    provider_name = "bailian_web_search"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen-plus",
        base_url: str = BAILIAN_NATIVE_SEARCH_URL,
        strategy: str = "turbo",
        timeout_seconds: float = 45.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.strategy = strategy
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        language: str = "zh-CN",
        country: str | None = "CN",
    ) -> SearchDiscoveryResult:
        prompt = (
            f"请检索与以下企业售前主题直接相关的公开网页：{query}\n"
            f"语言：{language}；国家或地区：{country or '不限'}。"
            "只用于发现来源，不要把未核验内容写成企业事实。"
        )
        payload = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": prompt}]},
            "parameters": {
                "result_format": "message",
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "enable_source": True,
                    "enable_citation": True,
                    "search_strategy": self.strategy,
                },
            },
        }
        try:
            response = await self.client.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "X-DashScope-SSE": "disable",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("provider returned non-object JSON")
            return self._parse(data, query, max_results, response.headers)
        except AppError:
            raise
        except httpx.HTTPStatusError as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "阿里云百炼搜索请求被拒绝",
                status_code=503,
                retryable=exc.response.status_code >= 500,
                details={"provider": self.provider_name, "http_status": exc.response.status_code},
            ) from exc
        except (httpx.RequestError, ValueError, TypeError, KeyError) as exc:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "阿里云百炼搜索暂时不可用或返回了无效数据",
                status_code=503,
                retryable=True,
                details={"provider": self.provider_name, "reason": type(exc).__name__},
            ) from exc

    def _parse(
        self,
        data: dict[str, Any],
        query: str,
        max_results: int,
        headers: httpx.Headers,
    ) -> SearchDiscoveryResult:
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        search_info = (
            output.get("search_info") if isinstance(output.get("search_info"), dict) else {}
        )
        raw_sources = search_info.get("search_results")
        if not isinstance(raw_sources, list):
            raw_sources = _find_compatible_sources(data)
        sources: list[SearchSource] = []
        seen: set[str] = set()
        for raw in raw_sources:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or raw.get("link") or "").strip()
            if not url or url in seen:
                continue
            title = str(raw.get("title") or raw.get("name") or raw.get("site_name") or url)
            snippet_value = raw.get("snippet") or raw.get("summary") or raw.get("text")
            site_name = raw.get("site_name") or raw.get("site")
            try:
                source = SearchSource(
                    title=title[:500],
                    url=url,
                    snippet=str(snippet_value)[:10_000] if snippet_value else None,
                    site_name=str(site_name)[:200] if site_name else None,
                    position=len(sources),
                )
            except ValueError:
                continue
            sources.append(source)
            seen.add(url)
            if len(sources) >= max_results:
                break
        if not sources:
            raise AppError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "阿里云百炼本次没有返回可用的公开来源",
                status_code=503,
                retryable=True,
                details={"provider": self.provider_name},
            )
        request_id = str(
            data.get("request_id")
            or headers.get("x-request-id")
            or headers.get("x-dashscope-request-id")
            or f"bws_{uuid.uuid4().hex}"
        )
        return SearchDiscoveryResult(
            provider=self.provider_name,
            provider_request_id=request_id,
            query=query,
            sources=sources,
            answer_summary=_answer_text(output),
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
        )

    async def test_connection(self) -> int:
        started = perf_counter()
        await self.search("阿里云官网", max_results=1)
        return round((perf_counter() - started) * 1000)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _answer_text(output: dict[str, Any]) -> str | None:
    choices = output.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])[:50_000]
    if output.get("text"):
        return str(output["text"])[:50_000]
    return None


def _find_compatible_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        data.get("search_results"),
        data.get("sources"),
        data.get("output", {}).get("sources")
        if isinstance(data.get("output"), dict)
        else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []
