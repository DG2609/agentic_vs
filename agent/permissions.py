"""
Tool permission system — per-tool allow/deny/ask rules with SQLite persistence.

Supports:
- Glob patterns for tool names and file paths
- Interactive prompts via async callback (set by TUI)
- Default: write tools = ask, read tools = allow
"""

import os
import sqlite3
import threading
import time
import fnmatch
import logging
import asyncio
from typing import Optional, Callable, Awaitable

import config

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(str(config.DATA_DIR), "permissions.db")

# Async callback for interactive prompts — set by TUI via set_permission_callback()
_permission_callback: Optional[Callable[..., Awaitable[str]]] = None

# Write tools default to "ask" when no rule exists
WRITE_TOOLS = frozenset({
    "file_edit", "file_write", "file_edit_batch", "terminal_exec",
    "git_add", "git_commit", "git_branch", "git_stash",
    "git_push", "git_pull", "git_fetch", "git_merge",
    "github_create_issue", "github_create_pr", "github_comment",
    "gitlab_create_issue", "gitlab_create_mr", "gitlab_comment",
    "skill_create", "skill_install", "skill_remove",
})


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permission_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_pattern TEXT NOT NULL,
            file_pattern TEXT DEFAULT '*',
            decision TEXT NOT NULL CHECK(decision IN ('allow', 'deny', 'ask')),
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def set_permission_callback(cb: Optional[Callable[..., Awaitable[str]]]) -> None:
    """Set async callback for interactive permission prompts."""
    global _permission_callback
    _permission_callback = cb


# ── Denial tracking (CC: DENIAL_LIMITS) ─────────────────────────
# After _MAX_CONSECUTIVE_DENIALS in a row, or _MAX_TOTAL_DENIALS total,
# the agent falls back to auto-allowing rather than looping forever.
_MAX_CONSECUTIVE_DENIALS = 3
_MAX_TOTAL_DENIALS = 20

_denial_state = threading.local()


def _get_counters():
    """Get (or initialize) per-thread denial counters."""
    if not hasattr(_denial_state, 'consecutive'):
        _denial_state.consecutive = 0
        _denial_state.total = 0
    return _denial_state


def record_denial() -> dict:
    """Increment denial counters. Returns {consecutive, total, bypass_active}."""
    s = _get_counters()
    s.consecutive += 1
    s.total += 1
    bypass = (
        s.consecutive >= _MAX_CONSECUTIVE_DENIALS
        or s.total >= _MAX_TOTAL_DENIALS
    )
    if bypass:
        logger.warning(
            f"[permissions] denial limit reached "
            f"(consecutive={s.consecutive}, total={s.total}) — auto-allowing"
        )
    return {
        "consecutive": s.consecutive,
        "total": s.total,
        "bypass_active": bypass,
    }


def record_allow() -> None:
    """Reset consecutive denial counter on a successful allow."""
    _get_counters().consecutive = 0


def reset_denial_counters() -> None:
    """Reset all denial counters (call at session start)."""
    s = _get_counters()
    s.consecutive = 0
    s.total = 0


def is_denial_bypass_active() -> bool:
    """True when denial thresholds are exceeded and agent should auto-allow."""
    s = _get_counters()
    return (
        s.consecutive >= _MAX_CONSECUTIVE_DENIALS
        or s.total >= _MAX_TOTAL_DENIALS
    )


def save_permission(tool_pattern: str, decision: str, file_pattern: str = "*") -> int:
    """Save a permission rule. Returns rule ID."""
    if decision not in ("allow", "deny", "ask"):
        raise ValueError(f"Invalid decision: {decision}")
    conn = _get_conn()
    try:
        # Remove conflicting rules for same pattern combo
        conn.execute(
            "DELETE FROM permission_rules WHERE tool_pattern = ? AND file_pattern = ?",
            (tool_pattern, file_pattern),
        )
        cursor = conn.execute(
            "INSERT INTO permission_rules (tool_pattern, file_pattern, decision, created_at) VALUES (?, ?, ?, ?)",
            (tool_pattern, file_pattern, decision, time.time()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def delete_permission(rule_id: int) -> bool:
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM permission_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def clear_permissions() -> int:
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM permission_rules")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def list_permissions() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, tool_pattern, file_pattern, decision, created_at "
            "FROM permission_rules ORDER BY created_at DESC"
        ).fetchall()
        return [
            {"id": r[0], "tool_pattern": r[1], "file_pattern": r[2],
             "decision": r[3], "created_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def get_decision(tool_name: str, file_path: str = "") -> str:
    """Get stored decision for a tool+file. Returns 'allow', 'deny', or 'ask'.

    Matching order: most recent matching rule wins.
    If no rule matches, defaults based on tool type (write=ask, read=allow).
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT tool_pattern, file_pattern, decision "
            "FROM permission_rules ORDER BY created_at DESC"
        ).fetchall()
        for tool_pat, file_pat, decision in rows:
            if fnmatch.fnmatch(tool_name, tool_pat):
                if not file_path or file_pat == "*" or fnmatch.fnmatch(file_path, file_pat):
                    return decision
        # Default: write tools = ask, read tools = allow
        return "ask" if tool_name in WRITE_TOOLS else "allow"
    finally:
        conn.close()


async def check_permission(tool_name: str, args: dict) -> tuple[bool, str]:
    """Check if a tool is allowed to run.

    Integrates denial tracking: after _MAX_CONSECUTIVE_DENIALS consecutive
    denials or _MAX_TOTAL_DENIALS total, auto-allows to prevent agent lock-up
    (matches CC's DENIAL_LIMITS behaviour).

    Returns:
        (allowed: bool, reason: str)
    """
    file_path = ""
    if isinstance(args, dict):
        file_path = args.get("file_path", args.get("command", ""))

    decision = get_decision(tool_name, file_path)

    if decision == "allow":
        record_allow()
        return True, ""
    if decision == "deny":
        record_denial()
        return False, f"Permission denied for {tool_name}"

    # decision == "ask" — check bypass first (CC: avoid infinite ask loops)
    if is_denial_bypass_active():
        logger.info(f"[permissions] bypass active — auto-allowing {tool_name}")
        record_allow()
        return True, "auto-allowed (denial limit reached)"

    # Prompt via callback
    if _permission_callback:
        try:
            result = await _permission_callback(tool_name, args)
            if result == "always_allow":
                save_permission(tool_name, "allow")
                record_allow()
                return True, ""
            elif result == "always_deny":
                save_permission(tool_name, "deny")
                record_denial()
                return False, f"Permission denied for {tool_name} (saved)"
            elif result in ("allow", "yes"):
                record_allow()
                return True, ""
            else:
                info = record_denial()
                reason = f"Permission denied for {tool_name}"
                if info["bypass_active"]:
                    reason += " — denial limit reached, future calls will auto-allow"
                return False, reason
        except Exception as e:
            logger.warning(f"Permission callback error for {tool_name}: {e}")
            return True, ""  # fail open

    # No callback (headless / CLI) — default allow
    record_allow()
    return True, ""
