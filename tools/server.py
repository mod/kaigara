import os

from fastapi import FastAPI

app = FastAPI(title="kaigara-tools")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tools"}


@app.post("/llm")
async def llm(payload: dict):
    """Proxy LLM calls — injects auth from env, never exposes keys."""
    # Placeholder: will proxy to OpenRouter / provider with auth header
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    has_key = bool(api_key and api_key != "placeholder")
    return {
        "message": "llm proxy placeholder",
        "has_api_key": has_key,
        "payload_keys": list(payload.keys()),
    }


@app.post("/tool/{name}")
async def tool(name: str, payload: dict):
    """Execute a tool by name."""
    return {
        "message": f"tool '{name}' placeholder",
        "payload": payload,
    }
