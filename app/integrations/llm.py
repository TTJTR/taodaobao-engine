from typing import Any


class MockLLMAdapter:
    async def complete_json(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "is_demo": True,
            "messages_received": len(messages),
            "schema_title": response_schema.get("title"),
        }
