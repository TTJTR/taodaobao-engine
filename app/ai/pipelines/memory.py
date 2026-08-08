from typing import Protocol

from app.ai.prompts.memory import MEMORY_SYSTEM_PROMPT, build_memory_user_prompt
from app.ai.schemas import (
    CustomerProfileDraft,
    MemorySuggestionStatus,
    ProfileMemoryProposal,
    ProfileMemorySuggestion,
)


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


async def suggest_profile_memory_updates(
    current_profile: CustomerProfileDraft,
    raw_text: str,
    source_ids: list[str],
    model_client: JsonModelClient,
) -> ProfileMemoryProposal:
    if not raw_text.strip():
        raise ValueError("raw_text must not be empty")
    normalized_source_ids = [source_id.strip() for source_id in source_ids if source_id.strip()]
    if not normalized_source_ids:
        raise ValueError("source_ids must not be empty")

    result = await model_client.generate_json(
        MEMORY_SYSTEM_PROMPT,
        build_memory_user_prompt(
            current_profile.model_dump_json(indent=2),
            raw_text,
            normalized_source_ids,
        ),
    )
    if set(result) != {"suggestions"} or not isinstance(result["suggestions"], list):
        raise ValueError("model output must contain only a suggestions list")

    allowed_sources = set(normalized_source_ids)
    suggestions: list[ProfileMemorySuggestion] = []
    for raw_suggestion in result["suggestions"]:
        if not isinstance(raw_suggestion, dict):
            raise ValueError("each memory suggestion must be an object")
        suggestion = ProfileMemorySuggestion.model_validate(
            {
                **raw_suggestion,
                "status": MemorySuggestionStatus.PENDING_CONFIRMATION,
            }
        )
        if not set(suggestion.source_ids).issubset(allowed_sources):
            raise ValueError("memory suggestion contains an unknown source_id")
        suggestions.append(suggestion)

    return ProfileMemoryProposal(
        current_profile=current_profile,
        suggestions=suggestions,
        confirmation_required=True,
    )
