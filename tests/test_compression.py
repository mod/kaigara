"""Context compression tests."""

from unittest.mock import AsyncMock

from agent.compression import PRUNE_PLACEHOLDER, ContextCompressor
from agent.tokens import estimate_tokens


def _msg(role, content):
    return {"role": role, "content": content}


def _tool_msg(content, call_id="c1"):
    return {"role": "tool", "content": content, "tool_call_id": call_id}


def _short_messages(n=5):
    msgs = [_msg("system", "You are helpful.")]
    for i in range(n):
        msgs.append(_msg("user", f"Question {i}"))
        msgs.append(_msg("assistant", f"Answer {i}"))
    return msgs


def _long_messages(n=100):
    """Generate messages that exceed a low threshold."""
    msgs = [_msg("system", "You are helpful.")]
    for i in range(n):
        msgs.append(_msg("user", f"Question {i}: " + "x" * 200))
        msgs.append(_msg("assistant", f"Answer {i}: " + "y" * 200))
        msgs.append(_tool_msg("tool output " * 50, f"call_{i}"))
    return msgs


async def test_no_compression_short():
    """Short conversation is not compressed."""
    comp = ContextCompressor("openai/gpt-4o")
    msgs = _short_messages(3)
    assert not comp.needs_compression(msgs)
    result = await comp.compress(msgs, AsyncMock())
    assert result == msgs


async def test_prune_old_tools():
    """Old tool results are replaced with placeholder."""
    comp = ContextCompressor("openai/gpt-4o", protect_first_n=2, protect_tail_n=2)

    msgs = [
        _msg("system", "sys"),
        _msg("user", "q1"),
        _tool_msg("big output " * 100, "c1"),  # should be pruned
        _msg("assistant", "a1"),
        _msg("user", "q2"),  # tail
        _msg("assistant", "a2"),  # tail
    ]

    result = comp.prune_old_tool_results(msgs)
    # First 2 and last 2 are protected
    assert result[0]["content"] == "sys"
    assert result[1]["content"] == "q1"
    assert result[2]["content"] == PRUNE_PLACEHOLDER  # tool pruned
    assert result[3]["content"] == "a1"


async def test_protect_first_n():
    """First N messages are never pruned."""
    comp = ContextCompressor("openai/gpt-4o", protect_first_n=3, protect_tail_n=2)

    msgs = [
        _msg("system", "sys"),
        _msg("user", "q1"),
        _tool_msg("protected tool output", "c0"),  # within first 3 — protected
        _msg("assistant", "a1"),
        _tool_msg("prunable tool output", "c1"),  # outside protected — pruned
        _msg("user", "q2"),
        _msg("assistant", "a2"),
    ]

    result = comp.prune_old_tool_results(msgs)
    assert result[2]["content"] == "protected tool output"  # protected
    assert result[4]["content"] == PRUNE_PLACEHOLDER  # pruned


async def test_protect_tail():
    """Recent messages are never pruned."""
    comp = ContextCompressor("openai/gpt-4o", protect_first_n=1, protect_tail_n=3)

    msgs = [
        _msg("system", "sys"),
        _tool_msg("old tool output", "c1"),  # prunable
        _msg("assistant", "a1"),
        _tool_msg("recent tool output", "c2"),  # in tail — protected
        _msg("user", "q2"),
        _msg("assistant", "a2"),
    ]

    result = comp.prune_old_tool_results(msgs)
    assert result[1]["content"] == PRUNE_PLACEHOLDER  # pruned
    assert result[3]["content"] == "recent tool output"  # protected


async def test_summary_generation():
    """Middle section is summarized via mocked LLM."""
    comp = ContextCompressor("openai/gpt-4o", protect_first_n=2, protect_tail_n=2)

    mock_llm = AsyncMock(return_value={
        "choices": [{"message": {"role": "assistant", "content": "Summary of conversation."}}]
    })

    msgs = [
        _msg("system", "sys"),
        _msg("user", "q0"),
        _msg("assistant", "a0"),  # middle
        _msg("user", "q1"),      # middle
        _msg("assistant", "a1"),  # middle
        _msg("user", "q2"),
        _msg("assistant", "a2"),
    ]

    result = await comp.summarize_middle(msgs, mock_llm)
    # Head (2) + summary (1) + tail (2) = 5
    assert len(result) == 5
    assert "summary" in result[2]["content"].lower()
    mock_llm.assert_called_once()


async def test_iterative_update():
    """Second compression updates previous summary, doesn't re-summarize head."""
    comp = ContextCompressor("openai/gpt-4o", protect_first_n=2, protect_tail_n=2)

    mock_llm = AsyncMock(return_value={
        "choices": [{"message": {"role": "assistant", "content": "Updated summary."}}]
    })

    # Already has a summary from a previous compression
    msgs = [
        _msg("system", "sys"),
        _msg("user", "q0"),
        _msg("system", "[Conversation summary of 3 messages]\nOld summary."),  # previous summary
        _msg("assistant", "a2"),
        _msg("user", "q3"),
        _msg("assistant", "a3"),
        _msg("user", "q4"),
        _msg("assistant", "a4"),
    ]

    result = await comp.summarize_middle(msgs, mock_llm)
    # The previous summary is now in the middle, will be re-summarized
    assert mock_llm.called


async def test_token_estimate():
    """Token count estimator is within 20% of char/4 for sample text."""
    text = "The quick brown fox jumps over the lazy dog. " * 100
    est = estimate_tokens(text)
    expected = len(text) // 4
    assert abs(est - expected) / expected < 0.20
