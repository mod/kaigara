"""Local environment — execute commands via subprocess or sandbox HTTP proxy."""

import asyncio
import os
import logging

import httpx

from tools.environments.base import BaseEnvironment, ExecResult

log = logging.getLogger(__name__)


class LocalEnvironment(BaseEnvironment):
    """Execute commands via local subprocess."""

    async def execute(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> ExecResult:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": workdir,
            "TERM": "dumb",
            "LANG": "C.UTF-8",
        }
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(stdout="", stderr="", exit_code=-1, timed_out=True)

        return ExecResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    async def read_file(self, path: str) -> str:
        from pathlib import Path
        p = Path(path)
        if not p.is_file():
            return f"error: file not found: {path}"
        return p.read_text()

    async def write_file(self, path: str, content: str) -> str:
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return "file written"


class SandboxProxyEnvironment(BaseEnvironment):
    """Proxy execution to kaigara sandbox container via HTTP."""

    def __init__(self, sandbox_url: str = "http://sandbox:9001"):
        self.sandbox_url = sandbox_url

    async def execute(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> ExecResult:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.sandbox_url}/exec",
                    json={"command": command, "workdir": workdir, "timeout": timeout},
                    timeout=timeout + 10,
                )
                data = resp.json()
                return ExecResult(
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    exit_code=data.get("exit_code", -1),
                    timed_out=data.get("timed_out", False),
                )
            except Exception as e:
                return ExecResult(stdout="", stderr=str(e), exit_code=-1)

    async def read_file(self, path: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.sandbox_url}/exec/read", json={"path": path}, timeout=10
            )
            if resp.status_code != 200:
                return f"error: {resp.json().get('detail', resp.text)}"
            return resp.json().get("content", "")

    async def write_file(self, path: str, content: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.sandbox_url}/exec/write",
                json={"path": path, "content": content},
                timeout=10,
            )
            if resp.status_code != 200:
                return f"error: {resp.json().get('detail', resp.text)}"
            return "file written"
