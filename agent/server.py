import os

import httpx
from fastapi import FastAPI

app = FastAPI(title="kaigara-agent")

TOOLS_URL = os.environ.get("TOOLS_URL", "http://tools:9000")
SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:9001")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent"}


@app.post("/chat")
async def chat(payload: dict):
    """Public chat endpoint. Proxies LLM calls through tools container."""
    async with httpx.AsyncClient() as client:
        llm_resp = await client.post(f"{TOOLS_URL}/llm", json=payload, timeout=60)
    return llm_resp.json()


@app.post("/shell")
async def shell(payload: dict):
    """Proxy shell commands to sandbox container."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{SANDBOX_URL}/exec", json=payload, timeout=30)
    return resp.json()
