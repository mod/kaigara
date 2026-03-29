"""Agent session endpoint tests."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import agent.server as srv


def _llm_text_response(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


async def test_chat_creates_session(agent_client, monkeypatch, tmp_path):
    """POST /chat creates a session in DB."""
    import agent.loop as agent_loop
    monkeypatch.setattr(
        agent_loop, "llm_call",
        AsyncMock(return_value=_llm_text_response("Hello!")),
    )

    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    resp = await agent_client.post(
        "/chat", json={"message": "hi", "model": "test-model"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] is not None

    # Verify session exists in DB
    from agent.state import SessionDB
    db = SessionDB(tmp_path / "kaigara.db")
    session = db.get_session(data["session_id"])
    assert session is not None
    assert len(session["messages"]) >= 2  # user + assistant


async def test_list_sessions_endpoint(agent_client, monkeypatch, tmp_path):
    """GET /sessions returns recent sessions."""
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    from agent.state import SessionDB
    db = SessionDB(tmp_path / "kaigara.db")
    db.create_session(model="m1")
    db.create_session(model="m2")

    resp = await agent_client.get("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


async def test_get_session_endpoint(agent_client, monkeypatch, tmp_path):
    """GET /sessions/{id} returns full message history."""
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    from agent.state import SessionDB
    db = SessionDB(tmp_path / "kaigara.db")
    sid = db.create_session()
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi")

    resp = await agent_client.get(f"/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == sid
    assert len(data["messages"]) == 2


async def test_get_session_not_found(agent_client, monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)
    resp = await agent_client.get("/sessions/nonexistent")
    assert resp.status_code == 404


async def test_search_sessions_endpoint(agent_client, monkeypatch, tmp_path):
    """GET /sessions/search?q=hello finds matching session."""
    monkeypatch.setattr(srv, "SESSIONS_DIR", tmp_path)

    from agent.state import SessionDB
    db = SessionDB(tmp_path / "kaigara.db")
    sid = db.create_session()
    db.add_message(sid, "user", "hello world")

    resp = await agent_client.get("/sessions/search", params={"q": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
