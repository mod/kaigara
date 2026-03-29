"""LLM client — calls providers directly with injected auth.

Supports Anthropic (native) and OpenRouter (OpenAI-compatible).
Normalizes all responses to OpenAI-compatible format for the agent loop.
"""

import json as _json
import logging
import os

import httpx

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _get_key(provider: str) -> str:
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(f"{provider} API key not configured")
    return key


def _is_anthropic(model: str) -> bool:
    return model.startswith("anthropic/") or model.startswith("claude-")


def _build_anthropic_request(req: dict, api_key: str) -> tuple[str, dict, dict]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    model = req["model"].removeprefix("anthropic/")

    messages = list(req["messages"])
    system_text = ""
    if messages and messages[0].get("role") == "system":
        system_text = messages[0].get("content", "")
        messages = messages[1:]

    # Convert OpenAI tool results to Anthropic format
    converted = []
    for msg in messages:
        if msg.get("role") == "tool":
            converted.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })
        elif msg.get("role") == "assistant" and msg.get("tool_calls"):
            content_blocks = []
            text = msg.get("content")
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tc in msg["tool_calls"]:
                args = tc["function"].get("arguments", "{}")
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": _json.loads(args) if isinstance(args, str) else args,
                })
            converted.append({"role": "assistant", "content": content_blocks})
        else:
            converted.append(msg)

    body: dict = {
        "model": model,
        "messages": converted,
        "max_tokens": req.get("max_tokens", 4096),
        "temperature": req.get("temperature", 0.7),
    }
    if system_text:
        body["system"] = system_text
    if req.get("tools"):
        anthropic_tools = []
        for tool in req["tools"]:
            if tool.get("type") == "function":
                fn = tool["function"]
                anthropic_tools.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                anthropic_tools.append(tool)
        body["tools"] = anthropic_tools
        tool_choice = req.get("tool_choice", "auto")
        if isinstance(tool_choice, dict):
            body["tool_choice"] = tool_choice
        elif tool_choice == "auto":
            body["tool_choice"] = {"type": "auto"}
        elif tool_choice == "none":
            body["tool_choice"] = {"type": "any"}
        else:
            body["tool_choice"] = {"type": "auto"}
    return ANTHROPIC_URL, headers, body


def _build_openrouter_request(req: dict, api_key: str) -> tuple[str, dict, dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    body: dict = {
        "model": req["model"],
        "messages": req["messages"],
        "max_tokens": req.get("max_tokens", 4096),
        "temperature": req.get("temperature", 0.7),
    }
    if req.get("tools"):
        body["tools"] = req["tools"]
        body["tool_choice"] = req.get("tool_choice", "auto")
    return OPENROUTER_URL, headers, body


def _anthropic_to_openai(data: dict) -> dict:
    content_blocks = data.get("content", [])
    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
    text = "".join(text_parts)

    tool_calls = []
    for i, block in enumerate(content_blocks):
        if block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", f"call_{i}"),
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": _json.dumps(block.get("input", {})),
                },
            })

    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls

    stop_map = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
    finish_reason = stop_map.get(data.get("stop_reason", ""), "stop")

    return {
        "id": data.get("id", ""),
        "object": "chat.completion",
        "model": data.get("model", ""),
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
            "total_tokens": (
                data.get("usage", {}).get("input_tokens", 0)
                + data.get("usage", {}).get("output_tokens", 0)
            ),
        },
    }


async def llm_call(request: dict) -> dict:
    """Call LLM provider directly. Returns OpenAI-compatible response dict."""
    model = request.get("model", "")
    is_anthropic = _is_anthropic(model)
    provider = "anthropic" if is_anthropic else "openrouter"
    api_key = _get_key(provider)

    if is_anthropic:
        url, headers, body = _build_anthropic_request(request, api_key)
    else:
        url, headers, body = _build_openrouter_request(request, api_key)

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body, timeout=120)

    if resp.status_code != 200:
        raise RuntimeError(f"LLM request failed: {resp.status_code} — {resp.text}")

    data = resp.json()

    if is_anthropic and "choices" not in data:
        data = _anthropic_to_openai(data)

    return data
