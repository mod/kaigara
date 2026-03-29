"""Gateway session management — tracks conversations per user/platform."""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class SessionSource:
    """Where a message came from."""
    platform: str
    chat_id: str
    chat_name: str = ""
    chat_type: str = "dm"  # dm, group, channel
    user_id: str = ""
    user_name: str = ""
    thread_id: str = ""


@dataclass
class GatewaySession:
    """A gateway conversation session."""
    key: str
    session_id: str | None = None  # kaigara agent session ID
    source: SessionSource | None = None
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def build_session_key(source: SessionSource) -> str:
    """Build a unique session key from message source."""
    parts = ["kaigara", source.platform, source.chat_type, source.chat_id]
    if source.thread_id:
        parts.append(source.thread_id)
    if source.chat_type in ("group", "channel") and source.user_id:
        parts.append(source.user_id)
    return ":".join(parts)


class SessionStore:
    """Manages gateway sessions. Persists to disk as JSON."""

    def __init__(self, data_dir: str):
        self._sessions: dict[str, GatewaySession] = {}
        self._data_dir = Path(data_dir) / "gateway"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_file = self._data_dir / "sessions.json"
        self._load()

    def _load(self):
        """Load sessions from disk."""
        if not self._sessions_file.exists():
            return
        try:
            data = json.loads(self._sessions_file.read_text())
            for key, entry in data.items():
                source_data = entry.get("source")
                source = SessionSource(**source_data) if source_data else None
                self._sessions[key] = GatewaySession(
                    key=key,
                    session_id=entry.get("session_id"),
                    source=source,
                    created_at=entry.get("created_at", time.time()),
                    updated_at=entry.get("updated_at", time.time()),
                )
        except Exception as e:
            log.error("Failed to load sessions: %s", e)

    def _save(self):
        """Persist sessions to disk."""
        data = {}
        for key, session in self._sessions.items():
            data[key] = {
                "session_id": session.session_id,
                "source": {
                    "platform": session.source.platform,
                    "chat_id": session.source.chat_id,
                    "chat_name": session.source.chat_name,
                    "chat_type": session.source.chat_type,
                    "user_id": session.source.user_id,
                    "user_name": session.source.user_name,
                    "thread_id": session.source.thread_id,
                } if session.source else None,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }
        self._sessions_file.write_text(json.dumps(data, indent=2))

    def get_or_create(self, source: SessionSource) -> GatewaySession:
        """Get existing session or create a new one."""
        key = build_session_key(source)
        if key not in self._sessions:
            self._sessions[key] = GatewaySession(key=key, source=source)
            self._save()
        session = self._sessions[key]
        session.updated_at = time.time()
        return session

    def reset_session(self, key: str):
        """Reset a session (start new conversation)."""
        if key in self._sessions:
            old = self._sessions[key]
            self._sessions[key] = GatewaySession(
                key=key, source=old.source,
            )
            self._save()

    def update_session_id(self, key: str, session_id: str):
        """Store the kaigara agent session ID."""
        if key in self._sessions:
            self._sessions[key].session_id = session_id
            self._sessions[key].updated_at = time.time()
            self._save()

    def get_expired(self, max_idle_minutes: int) -> list[str]:
        """Get session keys that have been idle too long."""
        now = time.time()
        cutoff = now - (max_idle_minutes * 60)
        return [
            key for key, s in self._sessions.items()
            if s.updated_at < cutoff
        ]
