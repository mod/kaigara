"""Docker/Podman environment — execute commands inside containers."""

import asyncio
import logging
import shutil
from pathlib import Path

from tools.environments.base import BaseEnvironment, ExecResult

log = logging.getLogger(__name__)

# Default container image
DEFAULT_IMAGE = "python:3.12-slim"


def _find_runtime() -> str | None:
    """Find docker or podman CLI."""
    for cmd in ("podman", "docker"):
        if shutil.which(cmd):
            return cmd
    return None


class DockerEnvironment(BaseEnvironment):
    """Execute commands in Docker or Podman containers.

    Creates a long-lived container and executes commands via `docker exec`.
    Supports persistent workspace via bind mounts.
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        runtime: str | None = None,
        workspace_dir: str | None = None,
        container_name: str | None = None,
        resource_limits: dict | None = None,
    ):
        self.image = image
        self.runtime = runtime or _find_runtime()
        self.workspace_dir = workspace_dir or str(Path.home() / ".kaigara" / "sandboxes" / "docker")
        self.container_name = container_name or "kaigara-sandbox-docker"
        self.resource_limits = resource_limits or {}
        self._container_id: str | None = None

    async def _ensure_container(self):
        """Start container if not already running."""
        if self._container_id:
            # Check if still running
            proc = await asyncio.create_subprocess_exec(
                self.runtime, "inspect", "-f", "{{.State.Running}}", self._container_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if stdout.strip() == b"true":
                return
            self._container_id = None

        # Check for existing container by name
        proc = await asyncio.create_subprocess_exec(
            self.runtime, "ps", "-aq", "-f", f"name={self.container_name}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        existing_id = stdout.decode().strip()
        if existing_id:
            # Start existing stopped container
            await asyncio.create_subprocess_exec(
                self.runtime, "start", existing_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            self._container_id = existing_id
            return

        # Create workspace directory
        Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)

        # Build run command
        cmd = [
            self.runtime, "run", "-d",
            "--name", self.container_name,
            "-v", f"{self.workspace_dir}:/workspace",
            "-w", "/workspace",
            "--cap-drop", "ALL",
            "--cap-add", "DAC_OVERRIDE",
            "--cap-add", "CHOWN",
            "--cap-add", "FOWNER",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
        ]

        # Resource limits
        cpus = self.resource_limits.get("cpus")
        memory = self.resource_limits.get("memory")
        if cpus:
            cmd.extend(["--cpus", str(cpus)])
        if memory:
            cmd.extend(["--memory", str(memory)])

        cmd.extend([self.image, "sleep", "86400"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start container: {stderr.decode()}")

        self._container_id = stdout.decode().strip()[:12]
        log.info("Started container %s (%s)", self.container_name, self._container_id)

    async def execute(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> ExecResult:
        if not self.runtime:
            return ExecResult(stdout="", stderr="error: neither docker nor podman found", exit_code=-1)

        await self._ensure_container()

        cmd = [
            self.runtime, "exec",
            "-w", workdir,
            self._container_id,
            "sh", "-c", command,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
        result = await self.execute(f"cat '{path}'", timeout=10)
        if result.exit_code != 0:
            return f"error: {result.stderr or 'file not found'}"
        return result.stdout

    async def write_file(self, path: str, content: str) -> str:
        # Write via heredoc to avoid escaping issues
        escaped = content.replace("'", "'\\''")
        result = await self.execute(
            f"mkdir -p \"$(dirname '{path}')\" && printf '%s' '{escaped}' > '{path}'",
            timeout=10,
        )
        if result.exit_code != 0:
            return f"error: {result.stderr}"
        return "file written"

    async def cleanup(self):
        """Stop and remove the container."""
        if self._container_id and self.runtime:
            await asyncio.create_subprocess_exec(
                self.runtime, "rm", "-f", self._container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._container_id = None
