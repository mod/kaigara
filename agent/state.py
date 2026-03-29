"""Session persistence — SQLite with WAL mode and FTS5 search."""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class SessionDB:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                model TEXT,
                system_prompt TEXT,
                started_at REAL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                timestamp REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, timestamp);

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content, content=messages, content_rowid=id
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.id, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.id, new.content);
            END;
        """)
        conn.commit()

    def create_session(
        self, model: str = "", system_prompt: str = ""
    ) -> str:
        session_id = uuid.uuid4().hex[:16]
        self._conn.execute(
            "INSERT INTO sessions (id, model, system_prompt, started_at) VALUES (?, ?, ?, ?)",
            (session_id, model, system_prompt, time.time()),
        )
        self._conn.commit()
        return session_id

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
    ):
        self._conn.execute(
            """INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                role,
                content,
                json.dumps(tool_calls) if tool_calls else None,
                tool_call_id,
                time.time(),
            ),
        )
        self._conn.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
            (session_id,),
        )
        self._conn.commit()

    def close_session(
        self,
        session_id: str,
        end_reason: str = "completed",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ):
        self._conn.execute(
            """UPDATE sessions
               SET ended_at = ?, end_reason = ?, prompt_tokens = ?, completion_tokens = ?
               WHERE id = ?""",
            (time.time(), end_reason, prompt_tokens, completion_tokens, session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        session = dict(row)
        messages = self._conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        session["messages"] = [self._msg_to_dict(m) for m in messages]
        return session

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT m.*, s.model, s.started_at as session_started
               FROM messages_fts fts
               JOIN messages m ON m.id = fts.rowid
               JOIN sessions s ON s.id = m.session_id
               WHERE messages_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def _msg_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        if d.get("tool_calls"):
            d["tool_calls"] = json.loads(d["tool_calls"])
        return d

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
