"""
Tests for session fork functionality.

Covers:
- fork_session tool: basic fork, fork at midpoint, fork at -1
- AgentState: parent_session_id and fork_point fields
- Edge cases: fork of nonexistent session, fork_at > message count
"""
import pytest


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_session_db(tmp_path, monkeypatch):
    """Redirect session store to a temp DB for isolation."""
    import agent.session_store as ss
    db_path = str(tmp_path / "fork_test_sessions.db")
    monkeypatch.setattr(ss, "_DB_PATH", db_path)
    yield


def _make_session(session_id: str, n_messages: int = 5):
    """Helper: create a session with n_messages in the store."""
    from agent.session_store import save_session, add_message
    save_session(session_id, title=f"Session {session_id}", workspace="/tmp/test")
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        add_message(session_id, role, f"message {i}")


# ── AgentState fields ─────────────────────────────────────────

def test_agent_state_has_fork_fields():
    from models.state import AgentState
    state = AgentState()
    assert state.parent_session_id is None
    assert state.fork_point is None


def test_agent_state_fork_fields_assignable():
    from models.state import AgentState
    state = AgentState(parent_session_id="parent-123", fork_point=7)
    assert state.parent_session_id == "parent-123"
    assert state.fork_point == 7


# ── fork_session tool ─────────────────────────────────────────

def test_fork_nonexistent_session():
    from agent.tools.session_tools import fork_session
    result = fork_session.invoke({"session_id": "no-such-session", "fork_at": -1})
    assert "not found" in result.lower() or "error" in result.lower()


def test_fork_at_end_copies_all_messages():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_messages, get_session

    _make_session("src1", n_messages=4)
    result = fork_session.invoke({"session_id": "src1", "fork_at": -1})

    assert "New session ID" in result
    # Extract new session ID from result
    new_id = None
    for line in result.splitlines():
        if "New session ID" in line:
            new_id = line.split(":")[-1].strip()
            break

    assert new_id is not None
    new_msgs = get_messages(new_id, limit=100)
    assert len(new_msgs) == 4

    session_meta = get_session(new_id)
    assert session_meta is not None
    assert session_meta["metadata"]["parent_session_id"] == "src1"
    assert session_meta["metadata"]["fork_point"] == 4


def test_fork_at_midpoint():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_messages

    _make_session("src2", n_messages=6)
    result = fork_session.invoke({"session_id": "src2", "fork_at": 3})

    new_id = None
    for line in result.splitlines():
        if "New session ID" in line:
            new_id = line.split(":")[-1].strip()
            break

    assert new_id is not None
    new_msgs = get_messages(new_id, limit=100)
    assert len(new_msgs) == 3  # messages[0:3]
    # First three messages
    assert new_msgs[0]["content"] == "message 0"
    assert new_msgs[1]["content"] == "message 1"
    assert new_msgs[2]["content"] == "message 2"


def test_fork_at_zero_creates_empty_fork():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_messages, get_session

    _make_session("src3", n_messages=4)
    result = fork_session.invoke({"session_id": "src3", "fork_at": 0})

    new_id = None
    for line in result.splitlines():
        if "New session ID" in line:
            new_id = line.split(":")[-1].strip()
            break

    assert new_id is not None
    new_msgs = get_messages(new_id, limit=100)
    assert len(new_msgs) == 0

    meta = get_session(new_id)
    assert meta["metadata"]["fork_point"] == 0


def test_fork_at_exceeds_count_copies_all():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_messages

    _make_session("src4", n_messages=3)
    result = fork_session.invoke({"session_id": "src4", "fork_at": 999})

    new_id = None
    for line in result.splitlines():
        if "New session ID" in line:
            new_id = line.split(":")[-1].strip()
            break

    assert new_id is not None
    new_msgs = get_messages(new_id, limit=100)
    assert len(new_msgs) == 3  # all messages copied


def test_fork_preserves_original_session():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_messages

    _make_session("src5", n_messages=5)
    fork_session.invoke({"session_id": "src5", "fork_at": 2})

    # Original still has all 5 messages
    orig_msgs = get_messages("src5", limit=100)
    assert len(orig_msgs) == 5


def test_fork_produces_unique_session_id():
    from agent.tools.session_tools import fork_session

    _make_session("src6", n_messages=3)

    def _extract_new_id(result: str) -> str:
        for line in result.splitlines():
            if "New session ID" in line:
                return line.split(":")[-1].strip()
        return ""

    result1 = fork_session.invoke({"session_id": "src6", "fork_at": -1})
    result2 = fork_session.invoke({"session_id": "src6", "fork_at": -1})
    id1 = _extract_new_id(result1)
    id2 = _extract_new_id(result2)
    assert id1 != id2
    assert id1 != "src6"
    assert id2 != "src6"


def test_fork_inherits_workspace_and_agent_mode():
    from agent.tools.session_tools import fork_session
    from agent.session_store import get_session, save_session, add_message

    save_session("src7", workspace="/projects/myapp", agent_mode="coder")
    add_message("src7", "user", "hello")

    result = fork_session.invoke({"session_id": "src7", "fork_at": -1})

    new_id = None
    for line in result.splitlines():
        if "New session ID" in line:
            new_id = line.split(":")[-1].strip()
            break

    assert new_id is not None
    meta = get_session(new_id)
    assert meta["workspace"] == "/projects/myapp"
    assert meta["agent_mode"] == "coder"
