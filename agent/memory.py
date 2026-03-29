"""Persistent cross-session memory — MEMORY.md and USER.md files."""

import fcntl
import os
import re
import tempfile
from pathlib import Path

DELIMITER = "§"
MEMORY_LIMIT = 2200  # chars
USER_LIMIT = 1375  # chars

# Patterns that suggest prompt injection or secret exfiltration
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all|prior)\s+(instructions|prompts)", re.I),
    re.compile(r"(system|admin)\s*prompt", re.I),
    re.compile(r"you\s+are\s+now", re.I),
    re.compile(r"disregard\s+(everything|all)", re.I),
]

SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI / OpenRouter keys
    re.compile(r"sk-ant-[a-zA-Z0-9]{20,}"),  # Anthropic keys
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),  # GitHub PATs
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),  # GitHub OAuth
    re.compile(r"xox[bp]-[a-zA-Z0-9-]{20,}"),  # Slack tokens
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access keys
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),  # Bearer tokens
]


class MemoryStore:
    def __init__(self, memory_dir: str | Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.memory_dir / "MEMORY.md"
        self.user_path = self.memory_dir / "USER.md"

    def read(self, file: str = "memory") -> str:
        path = self._resolve_path(file)
        if not path.exists():
            return ""
        return path.read_text()

    def add(self, entry: str, file: str = "memory") -> str:
        """Add an entry. Returns error string or empty on success."""
        if err := self._scan_entry(entry):
            return err

        path = self._resolve_path(file)
        limit = self._limit_for(file)

        with self._lock(path):
            current = path.read_text() if path.exists() else ""
            new_content = (current + DELIMITER + entry).lstrip(DELIMITER) if current else entry

            if len(new_content) > limit:
                return f"entry would exceed {limit} char limit ({len(new_content)} chars)"

            self._atomic_write(path, new_content)
        return ""

    def replace(self, old_text: str, new_text: str, file: str = "memory") -> str:
        """Replace text in a memory file. Returns error string or empty on success."""
        if err := self._scan_entry(new_text):
            return err

        path = self._resolve_path(file)
        limit = self._limit_for(file)

        with self._lock(path):
            current = path.read_text() if path.exists() else ""
            if old_text not in current:
                return f"old_text not found in {file}"

            new_content = current.replace(old_text, new_text, 1)
            if len(new_content) > limit:
                return f"replacement would exceed {limit} char limit"

            self._atomic_write(path, new_content)
        return ""

    def remove(self, text: str, file: str = "memory") -> str:
        """Remove an entry containing text. Returns error string or empty on success."""
        path = self._resolve_path(file)

        with self._lock(path):
            current = path.read_text() if path.exists() else ""
            entries = current.split(DELIMITER)
            new_entries = [e for e in entries if text not in e]

            if len(new_entries) == len(entries):
                return f"text not found in {file}"

            self._atomic_write(path, DELIMITER.join(new_entries))
        return ""

    def snapshot(self) -> str:
        """Return a frozen snapshot of both memory files for system prompt injection."""
        parts = []
        for label, file in [("Agent Memory", "memory"), ("User Preferences", "user")]:
            content = self.read(file)
            if content:
                parts.append(f"## {label}\n{content}")
        return "\n\n".join(parts) if parts else ""

    def _resolve_path(self, file: str) -> Path:
        if file == "user":
            return self.user_path
        return self.memory_path

    def _limit_for(self, file: str) -> int:
        return USER_LIMIT if file == "user" else MEMORY_LIMIT

    def _scan_entry(self, entry: str) -> str:
        """Scan for injection patterns and secrets. Returns error or empty."""
        for pattern in INJECTION_PATTERNS:
            if pattern.search(entry):
                return "rejected: entry contains prompt injection pattern"
        for pattern in SECRET_PATTERNS:
            if pattern.search(entry):
                return "rejected: entry contains secret/API key pattern"
        return ""

    def _lock(self, path: Path):
        """Context manager for file locking."""
        return _FileLock(path.with_suffix(".lock"))

    def _atomic_write(self, path: Path, content: str):
        """Write content atomically via temp file + rename."""
        fd, tmp_path = tempfile.mkstemp(dir=self.memory_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


class _FileLock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self._fd = open(self.path, "w")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()
