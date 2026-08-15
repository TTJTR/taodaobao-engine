"""Run one minimal live Bailian web-search probe without printing credentials."""

import asyncio
import os

from app.integrations.bailian_web_search import BailianWebSearchAdapter


async def main() -> None:
    api_key = (
        os.getenv("APP_BAILIAN_SEARCH_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise SystemExit("Bailian web-search API key is not configured")
    adapter = BailianWebSearchAdapter(api_key)
    try:
        result = await adapter.search("阿里云官方网站", max_results=1)
    finally:
        await adapter.aclose()
    source = result.sources[0]
    print(
        {
            "status": "ok",
            "provider": result.provider,
            "source_count": len(result.sources),
            "first_source_host": source.url.host,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
