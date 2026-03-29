import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent.clients import SandboxClient, ToolsClient
from agent.loop import AgentLoop
from agent.rbac import RBAC, Role
from agent.state import SessionDB

app = FastAPI(title="kaigara-agent")

TOOLS_URL = os.environ.get("TOOLS_URL", "http://tools:9000")
SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:9001")
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", "/data/sessions"))

rbac = RBAC()


def _get_db() -> SessionDB:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SessionDB(SESSIONS_DIR / "kaigara.db")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str | None = None
    role: str = "guest"
    model: str = "anthropic/claude-sonnet-4-20250514"
    system_prompt: str | None = None
    messages: list[dict] = []


class ChatResponse(BaseModel):
    response: str
    session_id: str | None = None
    messages: list[dict]
    tool_calls_made: int


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent"}


@app.get("/", response_class=HTMLResponse)
async def chat_page():
    """Simple HTML chat interface for testing."""
    return _CHAT_HTML


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Run the agent conversation loop with RBAC enforcement."""
    role = Role(req.role) if req.role in Role.__members__.values() else Role.GUEST
    tools_client = ToolsClient(TOOLS_URL)
    sandbox_client = SandboxClient(SANDBOX_URL)
    db = _get_db()

    # Session continuity: if session_id provided, load history from DB
    conversation_history = list(req.messages)
    if req.session_id and not conversation_history:
        session = db.get_session(req.session_id)
        if session:
            conversation_history = [
                {"role": m["role"], "content": m["content"]}
                for m in session.get("messages", [])
                if m.get("content")
            ]

    loop = AgentLoop(
        tools_client=tools_client,
        sandbox_client=sandbox_client,
        model=req.model,
        session_db=db,
        role=role,
        rbac=rbac,
    )

    result = await loop.run(
        user_message=req.message,
        conversation_history=conversation_history,
        system_prompt=req.system_prompt,
        session_id=req.session_id,
    )

    return ChatResponse(**result)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Streaming chat endpoint — returns SSE events."""
    role = Role(req.role) if req.role in Role.__members__.values() else Role.GUEST
    tools_client = ToolsClient(TOOLS_URL)
    sandbox_client = SandboxClient(SANDBOX_URL)
    db = _get_db()

    loop = AgentLoop(
        tools_client=tools_client,
        sandbox_client=sandbox_client,
        model=req.model,
        session_db=db,
        role=role,
        rbac=rbac,
    )

    async def event_stream() -> AsyncIterator[str]:
        result = await loop.run(
            user_message=req.message,
            conversation_history=req.messages,
            system_prompt=req.system_prompt,
            session_id=req.session_id,
        )

        # Emit token events for the response
        response_text = result["response"]
        for i in range(0, len(response_text), 20):
            chunk = response_text[i : i + 20]
            yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"

        # Emit done event
        yield f"event: done\ndata: {json.dumps({'session_id': result.get('session_id'), 'tool_calls_made': result['tool_calls_made']})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/sessions")
async def list_sessions(limit: int = 50):
    db = _get_db()
    return db.list_sessions(limit)


@app.get("/sessions/search")
async def search_sessions(q: str, limit: int = 20):
    db = _get_db()
    return db.search(q, limit)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    db = _get_db()
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.post("/shell")
async def shell(payload: dict):
    """Proxy shell commands to sandbox container."""
    sandbox_client = SandboxClient(SANDBOX_URL)
    command = payload.get("command", "")
    return await sandbox_client.exec(command)


_CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kaigara Chat</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
  #messages { flex: 1; overflow-y: auto; padding: 1rem; }
  .msg { margin-bottom: 0.75rem; padding: 0.5rem; border-radius: 4px; max-width: 80%; white-space: pre-wrap; }
  .msg.user { background: #16213e; margin-left: auto; }
  .msg.assistant { background: #0f3460; }
  #input-area { display: flex; padding: 0.5rem; background: #16213e; }
  #input { flex: 1; padding: 0.5rem; background: #1a1a2e; color: #e0e0e0; border: 1px solid #0f3460; border-radius: 4px; font-family: monospace; }
  #send { padding: 0.5rem 1rem; margin-left: 0.5rem; background: #e94560; color: white; border: none; border-radius: 4px; cursor: pointer; font-family: monospace; }
  #send:hover { background: #c73e54; }
</style>
</head>
<body>
<div id="messages"></div>
<div id="input-area">
  <input id="input" placeholder="Type a message..." autofocus>
  <button id="send">Send</button>
</div>
<script>
const msgs = document.getElementById('messages');
const input = document.getElementById('input');
const send = document.getElementById('send');
let sessionId = null;

function addMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMsg('user', text);

  const body = { message: text, role: 'guest' };
  if (sessionId) body.session_id = sessionId;

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    sessionId = data.session_id;
    addMsg('assistant', data.response || '(no response)');
  } catch (e) {
    addMsg('assistant', 'Error: ' + e.message);
  }
}

send.addEventListener('click', sendMessage);
input.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });
</script>
</body>
</html>"""
