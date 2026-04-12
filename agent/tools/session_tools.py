"""
Session management tools — fork, list, and inspect sessions.

Session fork: branches the current conversation at a given message index so
an alternative path can be explored without losing the original thread.
"""
import json
import time
from typing import Optional
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import BaseModel, Field

import config
from agent import session_store


# ── Schema ────────────────────────────────────────────────────

class ForkSessionArgs(BaseModel):
    """Arguments for forking a session."""
    session_id: str = Field(
        description="The session ID to fork. Use the current thread_id if forking the live session."
    )
    fork_at: int = Field(
        default=-1,
        description=(
            "Message index (0-based) at which to fork. "
            "-1 = fork at the current end (copy all messages). "
            "Positive values copy only messages[0:fork_at]."
        ),
    )


# ── Tool ─────────────────────────────────────────────────────

@tool(args_schema=ForkSessionArgs)
def fork_session(session_id: str, fork_at: int = -1) -> str:
    """Fork a session at a given message index to explore an alternative path.

    Creates a new session containing messages[0:fork_at] from the original
    session. The original session is preserved unchanged. The forked session
    can diverge from the fork point without affecting the parent.

    Args:
        session_id: ID of the session to fork.
        fork_at:    Message index to fork at (-1 = fork at current end).

    Returns:
        A string with the new session ID and how to switch to it.
    """
    # ── Fetch source session ─────────────────────────────────
    source = session_store.get_session(session_id)
    if source is None:
        return (
            f"Error: session '{session_id}' not found. "
            "Ensure the session has been saved to the session store."
        )

    # ── Fetch messages ────────────────────────────────────────
    all_messages = session_store.get_messages(session_id, limit=100_000)

    # ── Resolve fork_at index ──────────────────────────────────
    total = len(all_messages)
    if fork_at == -1 or fork_at >= total:
        fork_index = total
    elif fork_at < 0:
        # Negative indexing: -1 already handled above, -2 → second-to-last, etc.
        fork_index = max(0, total + fork_at + 1)
    else:
        fork_index = fork_at

    forked_messages = all_messages[:fork_index]

    # ── Create new session ────────────────────────────────────
    new_id = str(uuid4())
    fork_metadata = {
        "parent_session_id": session_id,
        "fork_point": fork_index,
        "forked_at": time.time(),
    }

    session_store.save_session(
        session_id=new_id,
        title=f"Fork of {source.get('title') or session_id[:8]} @msg{fork_index}",
        agent_mode=source.get("agent_mode", "planner"),
        workspace=source.get("workspace", config.WORKSPACE_DIR),
        message_count=0,
        total_tokens=0,
        metadata=fork_metadata,
    )

    # ── Copy messages into the fork ───────────────────────────
    for msg in forked_messages:
        session_store.add_message(
            session_id=new_id,
            role=msg["role"],
            content=msg["content"],
            tool_name=msg.get("tool_name"),
            tool_args=msg.get("tool_args"),
        )

    return (
        f"Session forked successfully.\n"
        f"  Parent session : {session_id}\n"
        f"  Fork point     : message {fork_index} of {total}\n"
        f"  New session ID : {new_id}\n"
        f"  Switch with    : /session {new_id}"
    )
