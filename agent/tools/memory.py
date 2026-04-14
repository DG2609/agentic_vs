"""
Tools: persistent memory — save, search, list, delete cross-session knowledge.

Uses SQLite (data/memory.db) for persistence. No embedding required —
search is done via full-text LIKE matching on key, value, and tags.
"""
import datetime
import json
import logging
import os
import sqlite3
import threading
import time
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.tools.truncation import truncate_output

import config
from models.tool_schemas import MemorySaveArgs, MemorySearchArgs, MemoryDeleteArgs, MemoryListArgs

# ── Limits ────────────────────────────────────────────────────
MAX_MEMORY_ENTRIES = 10_000  # prune oldest when exceeded
MAX_MEMORY_SIZE_MB = 50      # warn if DB file exceeds this

_logger = logging.getLogger(__name__)


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


def _prune_if_needed() -> None:
    """Prune oldest entries when count exceeds MAX_MEMORY_ENTRIES.

    Keeps the most recently accessed (updated_at) entries.
    Also logs a warning if the DB file exceeds MAX_MEMORY_SIZE_MB.
    """
    try:
        with _lock:
            conn = _get_conn()
            count = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            if count > MAX_MEMORY_ENTRIES:
                excess = count - MAX_MEMORY_ENTRIES
                # Delete the oldest entries (lowest updated_at)
                conn.execute(
                    """
                    DELETE FROM memory WHERE key IN (
                        SELECT key FROM memory ORDER BY updated_at ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                conn.commit()
                _logger.info("[memory] Pruned %d old entries (limit=%d)", excess, MAX_MEMORY_ENTRIES)
            conn.close()

        # Size check (non-blocking)
        try:
            size_mb = os.path.getsize(_DB_PATH) / (1024 * 1024)
            if size_mb > MAX_MEMORY_SIZE_MB:
                _logger.warning(
                    "[memory] DB size %.1f MB exceeds limit (%d MB). "
                    "Consider running memory_delete to clean up.",
                    size_mb, MAX_MEMORY_SIZE_MB,
                )
        except OSError:
            pass
    except Exception as e:
        _logger.warning("[memory] _prune_if_needed failed: %s", e)


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
        # Prune if entry count exceeds limit
        _prune_if_needed()
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
            except Exception as fts_err:
                # FTS failed → fallback to LIKE search
                _logger.warning("[memory] FTS5 search failed (%s), falling back to LIKE", fts_err)
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


# ── Stats + Batch Search ──────────────────────────────────────

@tool
def memory_stats() -> str:
    """Return statistics about the memory database: entry count, DB size, oldest/newest entries."""
    with _lock:
        conn = _get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            oldest = conn.execute("SELECT MIN(created_at) FROM memory").fetchone()[0]
            newest = conn.execute("SELECT MAX(created_at) FROM memory").fetchone()[0]
            # DB file size
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
            size_bytes = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
            size_kb = size_bytes / 1024

            lines = [
                "Memory database stats:",
                f"  Entries: {count}",
                f"  DB size: {size_kb:.1f} KB",
            ]
            if oldest:
                lines.append(f"  Oldest entry: {datetime.datetime.fromtimestamp(oldest).strftime('%Y-%m-%d')}")
            if newest:
                lines.append(f"  Newest entry: {datetime.datetime.fromtimestamp(newest).strftime('%Y-%m-%d')}")
            return "\n".join(lines)
        finally:
            conn.close()


class BatchMemorySearchArgs(BaseModel):
    queries: list[str] = Field(description="List of search queries to run in parallel.")
    k_per_query: int = Field(default=3, ge=1, le=10, description="Results per query.")


@tool(args_schema=BatchMemorySearchArgs)
def batch_memory_search(queries: list[str], k_per_query: int = 3) -> str:
    """Search memory with multiple queries and return merged, deduplicated results.

    Runs each query against the memory database independently and combines the
    results. Useful for retrieving context across several related topics at once.

    Args:
        queries: List of search queries (up to 10).
        k_per_query: Number of results to return per query (1-10).

    Returns:
        Combined results for all queries, labelled by query.
    """
    results = []
    for query in queries[:10]:  # cap at 10 queries
        try:
            raw = memory_search.invoke({"query": query, "n_results": k_per_query})
            results.append(f"[Query: {query}]\n{raw}")
        except Exception as e:
            results.append(f"[Query: {query}] Error: {e}")
    return truncate_output("\n\n".join(results))


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


# ── Export / Import ───────────────────────────────────────────

class MemoryExportArgs(BaseModel):
    file_path: str = Field(
        description="Destination JSON file path to write all memory entries."
    )


class MemoryImportArgs(BaseModel):
    file_path: str = Field(
        description="Source JSON file path exported by memory_export."
    )


@tool(args_schema=MemoryExportArgs)
def memory_export(file_path: str) -> str:
    """Export all memory entries to a JSON file for backup or migration.

    Args:
        file_path: Destination path for the JSON export file.

    Returns:
        Confirmation with entry count and file path.
    """
    try:
        with _lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT key, value, tags, created_at, updated_at FROM memory ORDER BY updated_at DESC"
            ).fetchall()
            conn.close()

        entries = [
            {
                "key": row["key"],
                "value": row["value"],
                "tags": json.loads(row["tags"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

        export_data = {
            "exported_at": time.time(),
            "count": len(entries),
            "entries": entries,
        }

        dest = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(dest) if os.path.dirname(dest) else ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        return f"✅ Exported {len(entries)} memory entries to {dest}"
    except Exception as e:
        return f"❌ Error exporting memory: {e}"


@tool(args_schema=MemoryImportArgs)
def memory_import(file_path: str) -> str:
    """Import memory entries from a JSON file created by memory_export.

    Deduplicates by key — existing entries are NOT overwritten unless the
    import entry has a newer updated_at timestamp.

    Args:
        file_path: Path to the JSON file to import.

    Returns:
        Summary of imported, skipped, and updated entries.
    """
    try:
        src = os.path.abspath(file_path)
        if not os.path.isfile(src):
            return f"❌ File not found: {src}"

        with open(src, encoding="utf-8") as f:
            data = json.load(f)

        entries = data.get("entries", [])
        if not entries:
            return "⚠️ No entries found in the export file."

        imported = skipped = updated = 0
        now = time.time()

        with _lock:
            conn = _get_conn()
            for entry in entries:
                key = entry.get("key", "")
                value = entry.get("value", "")
                tags = json.dumps(entry.get("tags", []))
                created_at = entry.get("created_at", now)
                updated_at = entry.get("updated_at", now)

                if not key:
                    skipped += 1
                    continue

                existing = conn.execute(
                    "SELECT updated_at FROM memory WHERE key = ?", (key,)
                ).fetchone()

                if existing is None:
                    conn.execute(
                        "INSERT INTO memory (key, value, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (key, value, tags, created_at, updated_at),
                    )
                    imported += 1
                elif updated_at > existing["updated_at"]:
                    conn.execute(
                        "UPDATE memory SET value=?, tags=?, updated_at=? WHERE key=?",
                        (value, tags, updated_at, key),
                    )
                    updated += 1
                else:
                    skipped += 1

            conn.commit()
            conn.close()

        return (
            f"✅ Memory import complete: {imported} new, {updated} updated, {skipped} skipped"
        )
    except Exception as e:
        return f"❌ Error importing memory: {e}"
