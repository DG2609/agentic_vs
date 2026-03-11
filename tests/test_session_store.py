"""
Tests for agent/session_store.py — SQLite session persistence.
"""
import os
import time
import pytest


@pytest.fixture(autouse=True)
def fresh_session_db(tmp_path, monkeypatch):
    """Point session store at a temp database for each test."""
    import agent.session_store as ss
    db_path = str(tmp_path / "test_sessions.db")
    monkeypatch.setattr(ss, "_DB_PATH", db_path)
    yield


# ── save / get ───────────────────────────────────────────────

def test_save_session_creates_record():
    from agent.session_store import save_session, get_session
    save_session("s1", title="First Session", workspace="/tmp/proj")
    rec = get_session("s1")
    assert rec is not None
    assert rec["session_id"] == "s1"
    assert rec["title"] == "First Session"
    assert rec["workspace"] == "/tmp/proj"
    assert rec["agent_mode"] == "planner"


def test_save_session_upsert_updates():
    from agent.session_store import save_session, get_session
    save_session("s2", title="Original")
    save_session("s2", title="Updated", agent_mode="coder")
    rec = get_session("s2")
    assert rec["title"] == "Updated"
    assert rec["agent_mode"] == "coder"


def test_get_session_unknown_returns_none():
    from agent.session_store import get_session
    assert get_session("nonexistent_id_xyz") is None


# ── messages ─────────────────────────────────────────────────

def test_add_message_increments_count():
    from agent.session_store import save_session, add_message, get_session
    save_session("s3")
    add_message("s3", "user", "hello")
    add_message("s3", "assistant", "hi back")
    rec = get_session("s3")
    assert rec["message_count"] == 2


def test_get_messages_chronological_order():
    from agent.session_store import save_session, add_message, get_messages
    save_session("s4")
    add_message("s4", "user", "first")
    add_message("s4", "assistant", "second")
    add_message("s4", "user", "third")
    msgs = get_messages("s4")
    assert len(msgs) == 3
    assert msgs[0]["content"] == "first"
    assert msgs[1]["content"] == "second"
    assert msgs[2]["content"] == "third"
    # Timestamps should be non-decreasing
    assert msgs[0]["timestamp"] <= msgs[1]["timestamp"] <= msgs[2]["timestamp"]


def test_get_messages_with_tool_info():
    from agent.session_store import save_session, add_message, get_messages
    save_session("s5")
    add_message("s5", "tool", "file contents...", tool_name="file_read", tool_args={"file_path": "main.py"})
    msgs = get_messages("s5")
    assert msgs[0]["tool_name"] == "file_read"
    assert msgs[0]["tool_args"]["file_path"] == "main.py"


# ── list_sessions ────────────────────────────────────────────

def test_list_sessions_most_recent_first():
    from agent.session_store import save_session, list_sessions
    save_session("old", title="Old Session")
    time.sleep(0.05)  # ensure different updated_at
    save_session("new", title="New Session")
    sessions = list_sessions()
    assert len(sessions) >= 2
    assert sessions[0]["session_id"] == "new"
    assert sessions[1]["session_id"] == "old"


def test_list_sessions_workspace_filter():
    from agent.session_store import save_session, list_sessions
    save_session("ws_a1", workspace="/projects/alpha")
    save_session("ws_a2", workspace="/projects/alpha")
    save_session("ws_b1", workspace="/projects/beta")
    filtered = list_sessions(workspace="/projects/alpha")
    assert len(filtered) == 2
    ids = {s["session_id"] for s in filtered}
    assert ids == {"ws_a1", "ws_a2"}


# ── delete ───────────────────────────────────────────────────

def test_delete_session_removes_record_and_messages():
    from agent.session_store import save_session, add_message, delete_session, get_session, get_messages
    save_session("del1")
    add_message("del1", "user", "msg")
    assert delete_session("del1") is True
    assert get_session("del1") is None
    assert get_messages("del1") == []


def test_delete_nonexistent_returns_false():
    from agent.session_store import delete_session
    assert delete_session("ghost_session") is False


# ── export ───────────────────────────────────────────────────

def test_export_session_includes_messages():
    from agent.session_store import save_session, add_message, export_session
    save_session("exp1", title="Export Test")
    add_message("exp1", "user", "question")
    add_message("exp1", "assistant", "answer")
    exported = export_session("exp1")
    assert exported is not None
    assert exported["session_id"] == "exp1"
    assert exported["title"] == "Export Test"
    assert "messages" in exported
    assert len(exported["messages"]) == 2
    assert exported["messages"][0]["content"] == "question"


def test_export_nonexistent_returns_none():
    from agent.session_store import export_session
    assert export_session("no_such_session") is None
