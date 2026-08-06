from typing import Any, Protocol


class AIEngine(Protocol):
    async def extract_profile(
        self, raw_text: str, source_ids: list[str]
    ) -> dict[str, Any]: ...

    async def extract_experience(
        self, raw_text: str, source_id: str
    ) -> dict[str, Any]: ...

    async def extract_capabilities(
        self, raw_text: str, source_id: str
    ) -> list[dict[str, Any]]: ...

    async def extract_search_intent(
        self, context: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]: ...


# dict is temporary at scaffold stage. Molly's Pydantic schemas will replace
# these return types without changing the method names or data flow.

