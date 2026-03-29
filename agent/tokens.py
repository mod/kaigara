"""Token counting and model context window limits."""

import json

# Approximate context windows for common models
CONTEXT_WINDOWS: dict[str, int] = {
    "anthropic/claude-sonnet-4-20250514": 200_000,
    "anthropic/claude-opus-4-20250514": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "openai/gpt-4o": 128_000,
    "openai/gpt-4o-mini": 128_000,
    "google/gemini-2.0-flash-001": 1_000_000,
}

DEFAULT_WINDOW = 128_000


def estimate_tokens(text: str) -> int:
    """Estimate token count. ~4 chars per token is a reasonable approximation."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            total += estimate_tokens(json.dumps(content))
        # Tool calls overhead
        if msg.get("tool_calls"):
            total += estimate_tokens(json.dumps(msg["tool_calls"]))
        # Per-message overhead (~4 tokens for role/formatting)
        total += 4
    return total


def context_window(model: str) -> int:
    """Get context window for a model."""
    return CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
