from copy import deepcopy
from typing import Any

MAX_CONVERSATION_MESSAGES = 20
MAX_CONVERSATION_CHARACTERS = 12_000
MAX_SUMMARY_CHARACTERS = 2_500
MAX_SUMMARY_ITEM_CHARACTERS = 160


def compact_context_payload(
    context: dict[str, Any],
    *,
    max_messages: int = MAX_CONVERSATION_MESSAGES,
    max_characters: int = MAX_CONVERSATION_CHARACTERS,
) -> dict[str, Any]:
    """Keep the current request/profile intact and compact only older chat turns."""
    compacted = deepcopy(context)
    history = compacted.get("conversation_history", [])
    if not isinstance(history, list):
        return compacted
    total_characters = sum(
        len(message.get("content", ""))
        for message in history
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )
    if len(history) <= max_messages and total_characters <= max_characters:
        return compacted

    recent: list[dict[str, Any]] = []
    recent_characters = 0
    recent_budget = max(1, max_characters - MAX_SUMMARY_CHARACTERS)
    for message in reversed(history):
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            continue
        if len(recent) >= max_messages - 1:
            break
        content_length = len(message["content"])
        if recent and recent_characters + content_length > recent_budget:
            break
        recent.append(message)
        recent_characters += content_length
    recent.reverse()
    omitted_count = max(0, len(history) - len(recent))
    omitted = history[:omitted_count]

    summary_parts: list[str] = []
    for message in omitted:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "unknown")
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        snippet = " ".join(content.split())[:MAX_SUMMARY_ITEM_CHARACTERS]
        summary_parts.append(f"[{role}] {snippet}")
    summary = "；".join(summary_parts)
    if len(summary) > MAX_SUMMARY_CHARACTERS:
        summary = summary[:MAX_SUMMARY_CHARACTERS] + "…"
    summary_message = {
        "role": "assistant",
        "content": f"较早 {omitted_count} 条对话的压缩摘要（仅保留原话片段）：{summary}",
    }
    compacted["conversation_history"] = [summary_message, *recent]
    return compacted
