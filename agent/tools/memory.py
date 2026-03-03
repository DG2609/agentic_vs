"""
Tools: persistent memory — save, search, list, delete cross-session knowledge.

Uses SQLite (data/memory.db) for persistence. No embedding required —
search is done via full-text LIKE matching on key, value, and tags.
"""
import json
import os
import sqlite3
import threading
import time
from langchain_core.tools import tool

import config
from models.tool_schemas import MemorySaveArgs, MemorySearchArgs, MemoryDeleteArgs, MemoryListArgs


# ── DB setup ───────────────────────────────────────────────────

_DB_PATH = os.path.join(str(config.DATA_DIR), "memory.db")
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    """Create memory table if it doesn't exist."""
    with _lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        # FTS virtual table for fast text search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(key, value, tags, content=memory, content_rowid=rowid)
        """)
        # Triggers to keep FTS in sync
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
                INSERT INTO memory_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
                INSERT INTO memory_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
            END
        """)
        conn.commit()
        conn.close()


# Initialize table on module load
try:
    _ensure_table()
except Exception:
    pass  # non-fatal: tools will report errors gracefully


# ── Tools ─────────────────────────────────────────────────────

@tool(args_schema=MemorySaveArgs)
def memory_save(key: str, value: str, tags: list = None) -> str:
    """Save or update a memory entry for cross-session knowledge retention.

    Use this to persist important facts, patterns, decisions, or findings
    that should be remembered in future sessions.

    Examples:
    - key="auth_pattern", value="Project uses JWT with refresh tokens stored in Redis"
    - key="db_schema", value="Users table has: id, email, created_at, role (enum)"
    - key="bug_2024_login", value="Login fails when email has uppercase — normalize before compare"

    Args:
        key: Unique identifier (descriptive name). Existing entries are overwritten.
        value: The information to remember.
        tags: Optional tags for filtering, e.g. ['architecture', 'bug', 'api'].

    Returns:
        Confirmation with key and character count.
    """
    if tags is None:
        tags = []

    now = time.time()
    tags_json = json.dumps(tags)

    try:
        with _lock:
            conn = _get_conn()
            # Atomic UPSERT — eliminates TOCTOU race between SELECT and INSERT/UPDATE.
            # ON CONFLICT preserves created_at from the original insert.
            conn.execute(
                """
                INSERT INTO memory (key, value, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    tags       = excluded.tags,
                    updated_at = excluded.updated_at
                """,
                (key, value, tags_json, now, now),
            )
            conn.commit()
            conn.close()
        tag_str = f" [tags: {', '.join(tags)}]" if tags else ""
        # Warn if the value looks like it might contain credentials
        _sensitive = ("api_key", "password", "secret", "token", "bearer", "private_key")
        warning = ""
        if any(s in value.lower() for s in _sensitive):
            warning = "\n⚠️  Warning: value may contain sensitive data (stored as plaintext)."
        return f"✅ Memory '{key}' saved ({len(value)} chars){tag_str}{warning}"
    except Exception as e:
        return f"❌ Error saving memory: {e}"


@tool(args_schema=MemorySearchArgs)
def memory_search(query: str, n_results: int = 5) -> str:
    """Search saved memory entries by keyword.

    Searches across keys, values, and tags using full-text search.
    Use this at the start of a session to recall relevant past knowledge.

    Args:
        query: Search terms to look for in memory entries.
        n_results: Maximum number of matching entries to return.

    Returns:
        Matching memory entries with keys, tags, and values.
    """
    try:
        with _lock:
            conn = _get_conn()
            # Try FTS5 MATCH (BM25 ranking)
            # Sanitize query: escape FTS5 special chars so a plain search phrase works
            fts_query = _sanitize_fts_query(query)
            try:
                rows = conn.execute(
                    """
                    SELECT m.key, m.value, m.tags, m.updated_at
                    FROM memory m
                    JOIN memory_fts fts ON m.rowid = fts.rowid
                    WHERE memory_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, n_results)
                ).fetchall()
            except Exception:
                # FTS failed → fallback to LIKE search
                like = f"%{query}%"
                rows = conn.execute(
                    """
                    SELECT key, value, tags, updated_at FROM memory
                    WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, n_results)
                ).fetchall()
            conn.close()

        if not rows:
            return f"No memory entries found for '{query}'"

        parts = [f"🧠 Found {len(rows)} memory entries for '{query}':\n"]
        for row in rows:
            tags = json.loads(row["tags"] or "[]")
            tag_str = f"  [tags: {', '.join(tags)}]" if tags else ""
            age = _human_age(row["updated_at"])
            parts.append(f"── {row['key']} (updated {age}){tag_str}")
            parts.append(f"   {row['value']}")
            parts.append("")

        return "\n".join(parts)
    except Exception as e:
        return f"❌ Error searching memory: {e}"


@tool(args_schema=MemoryListArgs)
def memory_list(tag: str = "") -> str:
    """List all memory entries, optionally filtered by tag.

    Args:
        tag: Filter entries to those with this tag. Empty = show all.

    Returns:
        Summary table of all matching memory entries (key, age, tags).
    """
    try:
        with _lock:
            conn = _get_conn()
            if tag:
                like = f'%"{tag}"%'
                rows = conn.execute(
                    "SELECT key, value, tags, updated_at FROM memory WHERE tags LIKE ? ORDER BY updated_at DESC",
                    (like,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key, value, tags, updated_at FROM memory ORDER BY updated_at DESC"
                ).fetchall()
            conn.close()

        if not rows:
            msg = f"No memory entries" + (f" with tag '{tag}'" if tag else "")
            return msg

        lines = [f"🧠 Memory entries ({len(rows)} total):\n"]
        for row in rows:
            tags = json.loads(row["tags"] or "[]")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            age = _human_age(row["updated_at"])
            preview = row["value"][:80].replace("\n", " ")
            if len(row["value"]) > 80:
                preview += "..."
            lines.append(f"  {row['key']}{tag_str}  (updated {age})")
            lines.append(f"    {preview}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error listing memory: {e}"


@tool(args_schema=MemoryDeleteArgs)
def memory_delete(key: str) -> str:
    """Delete a memory entry by key.

    Args:
        key: Key of the entry to delete.

    Returns:
        Confirmation or error.
    """
    try:
        with _lock:
            conn = _get_conn()
            result = conn.execute("DELETE FROM memory WHERE key = ?", (key,))
            conn.commit()
            deleted = result.rowcount
            conn.close()

        if deleted:
            return f"✅ Deleted memory entry '{key}'"
        return f"⚠️ No memory entry found with key '{key}'"
    except Exception as e:
        return f"❌ Error deleting memory: {e}"


# ── Helpers ───────────────────────────────────────────────────

def _sanitize_fts_query(query: str) -> str:
    """Escape FTS5 special characters and wrap in double-quotes for phrase search.

    FTS5 special chars: " * ^ ( ) AND OR NOT
    Strategy: strip/replace special chars, wrap each token in double quotes
    so the query becomes a safe multi-term OR search.
    """
    # Replace characters that break FTS5 syntax
    sanitized = query.replace('"', ' ').replace("'", ' ')
    # Remove other FTS5 operators
    for op in ['(', ')', '*', '^', '+', '-']:
        sanitized = sanitized.replace(op, ' ')
    # Split into tokens and quote each one
    tokens = sanitized.split()
    if not tokens:
        return '""'
    # Build: "token1" "token2" ... (implicit AND in FTS5)
    return ' '.join(f'"{t}"' for t in tokens if t)


def _human_age(ts: float) -> str:
    """Format a timestamp as a human-readable age."""
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"
