"""SQLite-backed plugin registry. Survives corrupt DB files by renaming
and starting fresh — we do not let a bad file block the agent from booting."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent.plugins.types import InstalledPlugin

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plugins (
  name TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  score INTEGER NOT NULL,
  permissions TEXT NOT NULL,
  install_path TEXT NOT NULL,
  installed_at TEXT NOT NULL,
  last_audited_at TEXT NOT NULL,
  last_error TEXT,
  raw_report TEXT
);
"""

_MIGRATIONS = [
    "ALTER TABLE plugins ADD COLUMN raw_report TEXT",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PluginRegistryDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            self._conn = conn
        except sqlite3.DatabaseError:
            logger.warning("plugins.db corrupt — renaming to .bak")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            bak = self.path.with_suffix(self.path.suffix + ".bak")
            if bak.exists():
                bak.unlink()
            self.path.replace(bak)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)

        # Additive migrations — tolerate columns already present.
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def upsert(
        self,
        *,
        name: str,
        version: str,
        status: str,
        score: int,
        permissions: list[str],
        install_path: str,
        last_error: str | None = None,
        raw_report: dict | None = None,
    ) -> None:
        now = _utc_now()
        cur = self._conn.execute("SELECT installed_at FROM plugins WHERE name=?", (name,))
        row = cur.fetchone()
        installed_at = row["installed_at"] if row else now
        report_json = json.dumps(raw_report) if raw_report is not None else None
        self._conn.execute(
            """
            INSERT INTO plugins(name, version, status, score, permissions,
                                install_path, installed_at, last_audited_at,
                                last_error, raw_report)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                version=excluded.version,
                status=excluded.status,
                score=excluded.score,
                permissions=excluded.permissions,
                install_path=excluded.install_path,
                last_audited_at=excluded.last_audited_at,
                last_error=excluded.last_error,
                raw_report=COALESCE(excluded.raw_report, plugins.raw_report)
            """,
            (
                name, version, status, score, json.dumps(permissions),
                install_path, installed_at, now, last_error, report_json,
            ),
        )
        self._conn.commit()

    def get_raw_report(self, name: str) -> dict | None:
        cur = self._conn.execute("SELECT raw_report FROM plugins WHERE name=?", (name,))
        row = cur.fetchone()
        if row is None or row["raw_report"] is None:
            return None
        try:
            return json.loads(row["raw_report"])
        except json.JSONDecodeError:
            return None

    def get(self, name: str) -> InstalledPlugin | None:
        cur = self._conn.execute("SELECT * FROM plugins WHERE name=?", (name,))
        row = cur.fetchone()
        return self._row_to_plugin(row) if row else None

    def list_all(self) -> list[InstalledPlugin]:
        cur = self._conn.execute("SELECT * FROM plugins ORDER BY name")
        return [self._row_to_plugin(r) for r in cur.fetchall()]

    def delete(self, name: str) -> bool:
        cur = self._conn.execute("DELETE FROM plugins WHERE name=?", (name,))
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_plugin(row: sqlite3.Row) -> InstalledPlugin:
        return InstalledPlugin(
            name=row["name"],
            version=row["version"],
            status=row["status"],
            score=row["score"],
            permissions=json.loads(row["permissions"]),
            install_path=row["install_path"],
            installed_at=row["installed_at"],
            last_audited_at=row["last_audited_at"],
            last_error=row["last_error"],
        )
