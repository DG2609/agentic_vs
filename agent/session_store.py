"""
Cross-platform session persistence — SQLite-based session store.

Stores conversation sessions so they can be resumed across CLI, TUI, Web, and Desktop.
Sessions are saved to data/sessions.db.
"""

import os
import json
import time
import sqlite3
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

import config

_DB_PATH = os.path.join(str(config.DATA_DIR), "sessions.db")


def _get_conn() -> sqlite3.Connection:
    """Get a connection to the sessions database."""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT DEFAULT '',
            agent_mode TEXT DEFAULT 'planner',
            workspace TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            tool_name TEXT DEFAULT NULL,
            tool_args TEXT DEFAULT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_messages_sid
        ON session_messages(session_id)
    """)
    conn.commit()
    return conn


def save_session(
    session_id: str,
    title: str = "",
    agent_mode: str = "planner",
    workspace: str = "",
    message_count: int = 0,
    total_tokens: int = 0,
    metadata: dict | None = None,
) -> None:
    """Create or update a session record."""
    now = time.time()
    meta_json = json.dumps(metadata or {})
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO sessions (session_id, title, agent_mode, workspace, created_at, updated_at, message_count, total_tokens, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                title = excluded.title,
                agent_mode = excluded.agent_mode,
                workspace = excluded.workspace,
                updated_at = excluded.updated_at,
                message_count = excluded.message_count,
                total_tokens = excluded.total_tokens,
                metadata = excluded.metadata
        """, (session_id, title, agent_mode, workspace, now, now, message_count, total_tokens, meta_json))
        conn.commit()
    finally:
        conn.close()


def add_message(
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
) -> None:
    """Add a message to a session's history."""
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO session_messages (session_id, role, content, timestamp, tool_name, tool_args)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, role, content, time.time(), tool_name, json.dumps(tool_args) if tool_args else None))
        conn.execute("""
            UPDATE sessions SET updated_at = ?, message_count = message_count + 1
            WHERE session_id = ?
        """, (time.time(), session_id))
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str) -> dict | None:
    """Get a session by ID."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT session_id, title, agent_mode, workspace, created_at, updated_at, message_count, total_tokens, metadata FROM sessions WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0],
            "title": row[1],
            "agent_mode": row[2],
            "workspace": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "message_count": row[6],
            "total_tokens": row[7],
            "metadata": json.loads(row[8]) if row[8] else {},
        }
    finally:
        conn.close()


def get_messages(session_id: str, limit: int = 100) -> list[dict]:
    """Get messages for a session, most recent first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, role, content, timestamp, tool_name, tool_args FROM session_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
        return [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "timestamp": r[3],
                "tool_name": r[4],
                "tool_args": json.loads(r[5]) if r[5] else None,
            }
            for r in reversed(rows)
        ]
    finally:
        conn.close()


def list_sessions(limit: int = 20, workspace: str = "") -> list[dict]:
    """List recent sessions, optionally filtered by workspace."""
    conn = _get_conn()
    try:
        if workspace:
            rows = conn.execute(
                "SELECT session_id, title, agent_mode, workspace, created_at, updated_at, message_count, total_tokens FROM sessions WHERE workspace = ? ORDER BY updated_at DESC LIMIT ?",
                (workspace, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT session_id, title, agent_mode, workspace, created_at, updated_at, message_count, total_tokens FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [
            {
                "session_id": r[0],
                "title": r[1],
                "agent_mode": r[2],
                "workspace": r[3],
                "created_at": r[4],
                "updated_at": r[5],
                "message_count": r[6],
                "total_tokens": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    """Delete a session and its messages."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
        cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def export_session(session_id: str) -> dict | None:
    """Export a full session (metadata + messages) as a JSON-serializable dict."""
    session = get_session(session_id)
    if not session:
        return None
    messages = get_messages(session_id, limit=10000)
    return {
        **session,
        "messages": messages,
    }
