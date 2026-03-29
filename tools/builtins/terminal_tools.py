"""Terminal tools — execute commands via configurable execution backends."""

import json
import logging
import os

from tools.environments.base import BaseEnvironment
from tools.environments.local import LocalEnvironment, SandboxProxyEnvironment

log = logging.getLogger(__name__)

# Cached environment instance
_env: BaseEnvironment | None = None


def _get_environment() -> BaseEnvironment:
    """Get or create the execution environment based on config."""
    global _env
    if _env is not None:
        return _env

    backend = os.environ.get("TERMINAL_ENV", "sandbox")

    if backend == "local":
        _env = LocalEnvironment()
    elif backend in ("docker", "podman"):
        from tools.environments.docker import DockerEnvironment
        image = os.environ.get("TERMINAL_CONTAINER_IMAGE", "python:3.12-slim")
        _env = DockerEnvironment(
            image=image,
            runtime=backend if backend == "podman" else None,
            resource_limits={
                "cpus": os.environ.get("TERMINAL_CONTAINER_CPUS"),
                "memory": os.environ.get("TERMINAL_CONTAINER_MEMORY"),
            },
        )
    else:
        # Default: proxy to sandbox container
        sandbox_url = os.environ.get("SANDBOX_URL", "http://sandbox:9001")
        _env = SandboxProxyEnvironment(sandbox_url)

    return _env


async def terminal(args: dict) -> str:
    """Execute a shell command."""
    command = args.get("command", "")
    if not command.strip():
        return json.dumps({"error": "empty command"})

    workdir = args.get("workdir", "/workspace")
    timeout = min(args.get("timeout", 30), 300)

    env = _get_environment()
    result = await env.execute(command, workdir=workdir, timeout=timeout)

    return json.dumps({
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    })


def _check_terminal() -> bool:
    """Check if terminal backend is available."""
    backend = os.environ.get("TERMINAL_ENV", "sandbox")
    if backend in ("docker", "podman"):
        import shutil
        return shutil.which(backend) is not None
    return True  # sandbox proxy and local always available


def register(registry):
    """Register terminal tools."""
    registry.register(
        name="terminal",
        description=(
            "Execute a shell command. Filesystem persists between calls. "
            "Use for running code, installing packages, file operations, git, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "workdir": {
                    "type": "string",
                    "description": "Working directory (default: /workspace)",
                    "default": "/workspace",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 300)",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
        handler=terminal,
        toolset="terminal",
        check_fn=_check_terminal,
        emoji="💻",
    )
