"""Context compression — keeps conversations within context window limits.

Two-stage approach:
1. Prune old tool results (cheap, no LLM call)
2. Summarize middle section (LLM call via tools container)
"""

import json
import logging

from agent.tokens import context_window, estimate_messages_tokens

log = logging.getLogger(__name__)

PRUNE_PLACEHOLDER = "[tool output pruned]"
SUMMARY_SYSTEM = (
    "Summarize the following conversation segment concisely. "
    "Preserve key facts, decisions, and tool results. "
    "Output only the summary, no preamble."
)


class ContextCompressor:
    def __init__(
        self,
        model: str,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_tail_n: int = 6,
        summary_target_ratio: float = 0.20,
    ):
        self.model = model
        self.window = context_window(model)
        self.threshold = int(self.window * threshold_percent)
        self.protect_first_n = protect_first_n
        self.protect_tail_n = protect_tail_n
        self.summary_target_ratio = summary_target_ratio

    def needs_compression(self, messages: list[dict]) -> bool:
        return estimate_messages_tokens(messages) > self.threshold

    def prune_old_tool_results(self, messages: list[dict]) -> list[dict]:
        """Stage 1: Replace old tool outputs with placeholder (no LLM call)."""
        if len(messages) <= self.protect_first_n + self.protect_tail_n:
            return messages

        result = []
        tail_start = len(messages) - self.protect_tail_n

        for i, msg in enumerate(messages):
            if i < self.protect_first_n or i >= tail_start:
                result.append(msg)
            elif msg.get("role") == "tool":
                result.append({**msg, "content": PRUNE_PLACEHOLDER})
            else:
                result.append(msg)

        return result

    async def summarize_middle(
        self, messages: list[dict], llm_call
    ) -> list[dict]:
        """Stage 2: Summarize the middle section via LLM call.

        Args:
            messages: The full message list (potentially after pruning)
            llm_call: async callable that takes an LLM request dict and returns response dict
        """
        if len(messages) <= self.protect_first_n + self.protect_tail_n:
            return messages

        head = messages[: self.protect_first_n]
        tail = messages[-self.protect_tail_n :]
        middle = messages[self.protect_first_n : -self.protect_tail_n]

        if not middle:
            return messages

        # Build summary request
        middle_text = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in middle
        )

        summary_request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": middle_text},
            ],
            "max_tokens": 1024,
            "temperature": 0.3,
        }

        try:
            resp = await llm_call(summary_request)
            choices = resp.get("choices", [])
            if choices:
                summary_text = choices[0].get("message", {}).get("content", "")
            else:
                summary_text = str(resp)
        except Exception as e:
            log.warning(f"compression summary failed: {e}")
            return messages

        summary_msg = {
            "role": "system",
            "content": f"[Conversation summary of {len(middle)} messages]\n{summary_text}",
        }

        return head + [summary_msg] + tail

    async def compress(self, messages: list[dict], llm_call) -> list[dict]:
        """Run full compression pipeline."""
        if not self.needs_compression(messages):
            return messages

        # Stage 1: prune tool results
        messages = self.prune_old_tool_results(messages)

        # Check if pruning was enough
        if not self.needs_compression(messages):
            return messages

        # Stage 2: summarize middle
        messages = await self.summarize_middle(messages, llm_call)
        return messages
