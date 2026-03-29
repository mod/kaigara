"""SessionDB unit tests."""

import threading

from agent.state import SessionDB


def _make_db(tmp_path) -> SessionDB:
    return SessionDB(tmp_path / "test.db")


def test_create_session(tmp_path):
    db = _make_db(tmp_path)
    sid = db.create_session(model="test-model", system_prompt="be helpful")
    assert len(sid) == 16
    session = db.get_session(sid)
    assert session["model"] == "test-model"
    assert session["system_prompt"] == "be helpful"
    assert session["started_at"] is not None


def test_add_messages(tmp_path):
    db = _make_db(tmp_path)
    sid = db.create_session()
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi there")

    session = db.get_session(sid)
    assert len(session["messages"]) == 2
    assert session["messages"][0]["role"] == "user"
    assert session["messages"][0]["content"] == "hello"
    assert session["messages"][1]["role"] == "assistant"
    assert session["message_count"] == 2


def test_fts_search(tmp_path):
    db = _make_db(tmp_path)
    sid = db.create_session()
    db.add_message(sid, "user", "quantum computing is fascinating")
    db.add_message(sid, "assistant", "indeed it is")

    results = db.search("quantum")
    assert len(results) >= 1
    assert "quantum" in results[0]["content"]


def test_session_close(tmp_path):
    db = _make_db(tmp_path)
    sid = db.create_session()
    db.close_session(sid, end_reason="budget", prompt_tokens=100, completion_tokens=200)

    session = db.get_session(sid)
    assert session["end_reason"] == "budget"
    assert session["prompt_tokens"] == 100
    assert session["completion_tokens"] == 200
    assert session["ended_at"] is not None


def test_concurrent_writes(tmp_path):
    db = _make_db(tmp_path)
    sid = db.create_session()
    errors = []

    def writer(n):
        try:
            # Each thread gets its own connection via thread-local
            db2 = SessionDB(tmp_path / "test.db")
            for i in range(20):
                db2.add_message(sid, "user", f"thread-{n}-msg-{i}")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent write errors: {errors}"
    session = db.get_session(sid)
    assert len(session["messages"]) == 60  # 3 threads * 20 messages


def test_persistence(tmp_path):
    db_path = tmp_path / "persist.db"
    db = SessionDB(db_path)
    sid = db.create_session(model="m1")
    db.add_message(sid, "user", "remember me")
    db.close()

    # Reopen
    db2 = SessionDB(db_path)
    session = db2.get_session(sid)
    assert session is not None
    assert session["model"] == "m1"
    assert len(session["messages"]) == 1


def test_wal_mode(tmp_path):
    db = _make_db(tmp_path)
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_list_sessions(tmp_path):
    db = _make_db(tmp_path)
    ids = [db.create_session(model=f"m{i}") for i in range(5)]
    sessions = db.list_sessions(limit=3)
    assert len(sessions) == 3
    # Most recent first
    assert sessions[0]["id"] == ids[-1]
