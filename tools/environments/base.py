"""Base environment — abstract interface for command execution backends."""

import abc
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

MAX_OUTPUT = 100 * 1024  # 100KB


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    def to_str(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout[:MAX_OUTPUT])
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr[:MAX_OUTPUT]}")
        if self.timed_out:
            parts.append("[timed out]")
        if not parts:
            parts.append(f"(exit code {self.exit_code})")
        return "\n".join(parts)


class BaseEnvironment(abc.ABC):
    """Abstract execution backend."""

    @abc.abstractmethod
    async def execute(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> ExecResult:
        ...

    @abc.abstractmethod
    async def read_file(self, path: str) -> str:
        ...

    @abc.abstractmethod
    async def write_file(self, path: str, content: str) -> str:
        ...

    async def cleanup(self):
        """Optional cleanup hook."""
        pass
