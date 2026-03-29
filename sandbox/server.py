import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from sandbox.models import (
    ExecRequest,
    ExecResponse,
    FileReadRequest,
    FileWriteRequest,
)

app = FastAPI(title="kaigara-sandbox")

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
MAX_OUTPUT = 100 * 1024  # 100KB

# Only these env vars are allowed in subprocess
SCRUBBED_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": str(WORKSPACE),
    "TERM": "dumb",
    "LANG": "C.UTF-8",
}


def _resolve_workdir(workdir: str) -> Path:
    """Resolve workdir and ensure it stays under WORKSPACE."""
    if workdir.startswith("/workspace"):
        # Absolute path within workspace — remap to our WORKSPACE
        relative = workdir[len("/workspace") :].lstrip("/")
        resolved = (WORKSPACE / relative).resolve()
    else:
        resolved = (WORKSPACE / workdir).resolve()

    if not (resolved == WORKSPACE or str(resolved).startswith(str(WORKSPACE) + os.sep)):
        raise HTTPException(status_code=400, detail="workdir must be under /workspace")
    return resolved


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sandbox"}


@app.post("/exec", response_model=ExecResponse)
async def exec_command(req: ExecRequest):
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="empty command")

    workdir = _resolve_workdir(req.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Build env: scrubbed base + caller-provided non-secret vars
    env = {**SCRUBBED_ENV, **req.env}

    proc = await asyncio.create_subprocess_shell(
        req.command,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=req.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        stdout = b""
        stderr = b""
        timed_out = True

    return ExecResponse(
        stdout=stdout.decode(errors="replace")[:MAX_OUTPUT],
        stderr=stderr.decode(errors="replace")[:MAX_OUTPUT],
        exit_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
    )


@app.post("/exec/write")
async def exec_write(req: FileWriteRequest):
    path = _resolve_workdir(req.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(req.content)
    return {"status": "ok", "path": str(path)}


@app.post("/exec/read")
async def exec_read(req: FileReadRequest):
    path = _resolve_workdir(req.path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {req.path}")
    return {"content": path.read_text(), "path": str(path)}
