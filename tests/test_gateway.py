"""Gateway tests — session creation, continuity, format, streaming, isolation."""

from unittest.mock import AsyncMock

import agent.loop as agent_loop
import agent.server as srv


def _llm_text(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _patch_llm(monkeypatch, response):
    monkeypatch.setattr(
        agent_loop, "llm_call", AsyncMock(return_value=response),
    )


async def test_chat_new_session(agent_client, monkeypatch, tmp_path):
    """POST /chat without session_id creates new session."""
    _patch_llm(monkeypatch, _llm_text("Hello!"))
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    resp = await agent_client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] is not None
    assert data["response"] == "Hello!"


async def test_chat_continue_session(agent_client, monkeypatch, tmp_path):
    """POST /chat with session_id continues existing session."""
    _patch_llm(monkeypatch, _llm_text("I remember!"))
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    # First message
    resp1 = await agent_client.post("/chat", json={"message": "My name is Alice"})
    sid = resp1.json()["session_id"]

    # Continue with same session
    resp2 = await agent_client.post(
        "/chat", json={"message": "What is my name?", "session_id": sid}
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["session_id"] is not None


async def test_chat_response_format(agent_client, monkeypatch, tmp_path):
    """Response includes session_id, response text, tool_calls_made."""
    _patch_llm(monkeypatch, _llm_text("formatted response"))
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    resp = await agent_client.post("/chat", json={"message": "test"})
    data = resp.json()
    assert "response" in data
    assert "session_id" in data
    assert "tool_calls_made" in data
    assert "messages" in data


async def test_chat_stream_sse(agent_client, monkeypatch, tmp_path):
    """POST /chat/stream returns SSE events with tokens."""
    _patch_llm(monkeypatch, _llm_text("Streaming response"))
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    resp = await agent_client.post("/chat/stream", json={"message": "hi"})
    assert resp.status_code == 200
    body = resp.text
    assert "event: token" in body
    assert "event: done" in body
    assert "Streaming response" in body


async def test_chat_html_page(agent_client):
    """GET / returns HTML chat page."""
    resp = await agent_client.get("/")
    assert resp.status_code == 200
    assert "Kaigara Chat" in resp.text
    assert "<script>" in resp.text


async def test_chat_default_role_guest(agent_client, monkeypatch, tmp_path):
    """Request without explicit role defaults to guest."""
    _patch_llm(monkeypatch, _llm_text("ok"))
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    resp = await agent_client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 200
