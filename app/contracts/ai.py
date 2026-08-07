from typing import Any, Protocol


class AIEngine(Protocol):
    async def extract_profile(self, raw_text: str, source_ids: list[str]) -> dict[str, Any]: ...

    async def extract_experience(self, raw_text: str, source_id: str) -> dict[str, Any]: ...

    async def extract_capabilities(self, raw_text: str, source_id: str) -> list[dict[str, Any]]: ...

    async def extract_search_intent(self, context: dict[str, Any]) -> dict[str, Any]: ...

    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]: ...


# The cross-service shape stays dict/list for backend compatibility. Implementations
# validate every input/output with app.ai.schemas before returning these values.
