import httpx
import pytest

from app.core.errors import AppError, ErrorCode
from app.integrations.bailian_web_search import BailianWebSearchAdapter
from app.schemas.v2 import (
    CreateAutomaticSearchRunRequest,
    CreateCombinedSearchRunRequest,
)


@pytest.mark.asyncio
async def test_bailian_search_forces_real_search_and_parses_sources() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = __import__("json").loads(request.content)
        assert payload["parameters"]["enable_search"] is True
        assert payload["parameters"]["search_options"] == {
            "forced_search": True,
            "enable_source": True,
            "enable_citation": True,
            "search_strategy": "turbo",
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "request_id": "request-safe-id",
                "output": {
                    "choices": [{"message": {"content": "只用于来源发现"}}],
                    "search_info": {
                        "search_results": [
                            {
                                "title": "阿里云官网",
                                "url": "https://www.aliyun.com/",
                                "snippet": "阿里云官方网站",
                                "site_name": "阿里云",
                            },
                            {
                                "title": "重复来源",
                                "url": "https://www.aliyun.com/",
                            },
                        ]
                    },
                },
                "usage": {"input_tokens": 10},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = BailianWebSearchAdapter("test-secret", client=client)
    try:
        result = await adapter.search("阿里云", max_results=5)
    finally:
        await client.aclose()

    assert result.provider == "bailian_web_search"
    assert result.provider_request_id == "request-safe-id"
    assert len(result.sources) == 1
    assert str(result.sources[0].url) == "https://www.aliyun.com/"
    assert result.answer_summary == "只用于来源发现"


@pytest.mark.asyncio
async def test_bailian_search_never_turns_missing_sources_into_mock_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"request_id": "empty", "output": {"search_info": {"search_results": []}}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = BailianWebSearchAdapter("test-secret", client=client)
    try:
        with pytest.raises(AppError) as captured:
            await adapter.search("不存在的搜索")
    finally:
        await client.aclose()

    assert captured.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_customer_profile_automatic_search_requires_profile() -> None:
    with pytest.raises(ValueError):
        CreateAutomaticSearchRunRequest(
            query="客户招聘与数字化信号",
            purpose="customer_profile",
        )

    request = CreateAutomaticSearchRunRequest(
        query="行业项目机会",
        purpose="solution",
    )
    assert request.profile_id is None


def test_combined_search_is_profile_only_and_has_cost_guardrails() -> None:
    profile_id = __import__("uuid").uuid4()
    request = CreateCombinedSearchRunRequest(
        query="客户公开情报与交叉验证",
        profile_id=profile_id,
    )

    assert request.purpose == "customer_profile"
    assert request.profile_id == profile_id
    assert request.max_cost_usd == 2.0
    assert request.max_tool_calls == 50
    assert request.allowed_fields

    with pytest.raises(ValueError):
        CreateCombinedSearchRunRequest(
            query="重复字段不能进入 Provider",
            profile_id=profile_id,
            allowed_fields=["industry", "industry"],
        )
