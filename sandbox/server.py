import asyncio

from fastapi import FastAPI

app = FastAPI(title="kaigara-sandbox")

WORKSPACE = "/workspace"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sandbox"}


@app.post("/exec")
async def exec_command(payload: dict):
    """Execute a shell command in the workspace. No secrets in this container."""
    command = payload.get("command", "")
    if not command:
        return {"error": "no command provided"}

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=WORKSPACE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": WORKSPACE, "TERM": "dumb"},
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    return {
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "exit_code": proc.returncode,
    }
