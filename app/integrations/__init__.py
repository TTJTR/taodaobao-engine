"""Feishu and model provider adapters."""

from app.integrations.ai_engine import MockAIEngine
from app.integrations.feishu import LiveFeishuAdapter, MockFeishuAdapter
from app.integrations.llm import MockLLMAdapter
from app.integrations.protocols import (
    FeishuAdapter,
    FeishuCreatedDocument,
    FeishuCreatedGroup,
    FeishuDocument,
    FeishuResource,
    FeishuTokenInfo,
    FeishuUserInfo,
    LLMAdapter,
)
from app.integrations.web_scraper import WebScrapeArtifact, WebScraperAdapter

__all__ = [
    "FeishuAdapter",
    "FeishuCreatedDocument",
    "FeishuCreatedGroup",
    "FeishuDocument",
    "FeishuResource",
    "FeishuTokenInfo",
    "FeishuUserInfo",
    "LLMAdapter",
    "LiveFeishuAdapter",
    "MockFeishuAdapter",
    "MockAIEngine",
    "MockLLMAdapter",
    "WebScrapeArtifact",
    "WebScraperAdapter",
]
