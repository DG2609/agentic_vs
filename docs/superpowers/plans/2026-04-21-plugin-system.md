# Plugin System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a cohesive plugin manager with install-time quality gates and subprocess-level runtime isolation, unifying the scattered `plugin_registry` / `skill_hub` / `skill_loader` modules under one facade.

**Architecture:** One `PluginManager` facade fronts five collaborators (`HubScout`, `Installer`, `QualityAuditor`, `RuntimeSandbox`, `PluginRegistryDB`). Each plugin runs in its own subprocess talking JSON-RPC 2.0 over stdio with monkey-patched network / filesystem / subprocess gates. Ink CLI gets a 4-step install wizard driven by Socket.IO events.

**Tech Stack:** Python 3.12, aiohttp, SQLite, subprocess + asyncio, LangChain `@tool`, React/Ink 5, Socket.IO, ruff/mypy/bandit (reused from QualityIntel).

**Spec:** `docs/superpowers/specs/2026-04-21-plugin-system-design.md`

---

## File structure

**New Python modules** (all under 300 lines each):

```
agent/plugins/
  __init__.py              — re-exports PluginManager, PluginMeta, QualityReport
  types.py                 — dataclasses: PluginMeta, QualityReport, InstalledPlugin
  registry_db.py           — SQLite persistence
  hub_scout.py             — fetch + search + cache hub index
  installer.py             — download, verify, promote to install dir
  auditor.py               — static quality + manifest audit
  sandbox.py               — RuntimeSandbox + ProxyTool (host side)
  manager.py               — PluginManager facade
shadowdev/
  __init__.py              — empty
  plugin_host.py           — subprocess-side JSON-RPC server
```

**New Ink/TS files:**

```
ink-cli/src/hooks/usePlugins.ts
ink-cli/src/components/PluginPicker.tsx
ink-cli/src/components/InstallWizard.tsx
ink-cli/src/components/QualityReport.tsx
```

**Modified files:**

- `server/main.py` — 7 routes + global `plugin_manager` + cleanup.
- `agent/graph.py` — call `plugin_manager.load_all()` alongside existing `get_plugin_tools()`.
- `ink-cli/src/App.tsx` — wire `usePlugins`, render picker/wizard, `/plugin*` slash commands.
- `ink-cli/src/components/InputBox.tsx` — 4 new slash commands.

**Test files:**

```
tests/plugins/
  __init__.py
  conftest.py              — shared fixtures (tmp_plugin_dir, fake_hub)
  fixtures/
    echo_plugin/           — benign test plugin
    hostile_plugin/        — eval, requests, subprocess at module level
    slow_plugin/           — sleeps 60s in a tool
  test_registry_db.py
  test_hub_scout.py
  test_installer.py
  test_auditor.py
  test_rpc_contract.py
  test_sandbox_boundaries.py
  test_manager_integration.py
  bench_e2e.sh             — manual bench (not run in CI)
```

---

## Task 1: Package scaffolding + shared types

Lays down directory structure and the four dataclasses every other module imports. Nothing has logic yet — this task only establishes the vocabulary.

**Files:**
- Create: `agent/plugins/__init__.py`
- Create: `agent/plugins/types.py`
- Create: `shadowdev/__init__.py`
- Create: `tests/plugins/__init__.py`
- Test: `tests/plugins/test_types.py`

- [ ] **Step 1: Create directories**

```bash
mkdir -p D:/agentic/agent/plugins D:/agentic/shadowdev D:/agentic/tests/plugins/fixtures
```

- [ ] **Step 2: Write failing test for dataclass shape**

Create `tests/plugins/test_types.py`:

```python
from agent.plugins.types import PluginMeta, QualityReport, InstalledPlugin, QualityIssue


def test_plugin_meta_defaults():
    m = PluginMeta(name="demo", version="1.0.0", url="https://x/demo.tar.gz", sha256="a" * 64)
    assert m.author == ""
    assert m.permissions == []
    assert m.signature is None


def test_quality_report_blocked():
    r = QualityReport(score=45, issues=[], blockers=[QualityIssue(rule="eval", message="eval at top level", severity="high")])
    assert r.blocked is True  # blocked if blockers present OR score<60


def test_quality_report_pass():
    r = QualityReport(score=80, issues=[], blockers=[])
    assert r.blocked is False


def test_installed_plugin_repr():
    p = InstalledPlugin(
        name="demo", version="1.0.0", status="installed",
        score=90, permissions=["net.http"], install_path="/x", installed_at="2026-04-21T00:00:00Z",
        last_audited_at="2026-04-21T00:00:00Z", last_error=None,
    )
    assert "demo" in repr(p)
```

- [ ] **Step 3: Run test — expect ImportError**

Run: `cd D:/agentic && pytest tests/plugins/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.plugins'`

- [ ] **Step 4: Implement types.py**

Create `agent/plugins/types.py`:

```python
"""Shared dataclasses for the plugin system."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


Severity = Literal["high", "medium", "low"]


@dataclass
class PluginMeta:
    """Hub metadata for a plugin available to install."""
    name: str
    version: str
    url: str
    sha256: str
    author: str = ""
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    tool_count: int = 0
    size_bytes: int = 0
    signature: str | None = None   # optional ed25519 detached signature (hex)


@dataclass
class QualityIssue:
    """Single finding from the auditor."""
    rule: str
    message: str
    severity: Severity
    file: str = ""
    line: int = 0


@dataclass
class QualityReport:
    """Result of a plugin audit run."""
    score: int                      # 0..100
    issues: list[QualityIssue] = field(default_factory=list)
    blockers: list[QualityIssue] = field(default_factory=list)
    raw: dict = field(default_factory=dict)   # raw tool output for debugging

    @property
    def blocked(self) -> bool:
        return bool(self.blockers) or self.score < 60


@dataclass
class InstalledPlugin:
    """Row from the plugins registry DB."""
    name: str
    version: str
    status: Literal["installed", "disabled", "error"]
    score: int
    permissions: list[str]
    install_path: str
    installed_at: str               # ISO-8601 UTC
    last_audited_at: str            # ISO-8601 UTC
    last_error: str | None = None
```

Create `agent/plugins/__init__.py`:

```python
"""ShadowDev plugin system — install, audit, sandbox."""
from agent.plugins.types import (
    PluginMeta,
    QualityIssue,
    QualityReport,
    InstalledPlugin,
)

__all__ = ["PluginMeta", "QualityIssue", "QualityReport", "InstalledPlugin"]
```

Create `shadowdev/__init__.py` (empty file).
Create `tests/plugins/__init__.py` (empty file).

- [ ] **Step 5: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_types.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add agent/plugins/ shadowdev/ tests/plugins/
git commit -m "feat(plugins): scaffold package + shared dataclasses"
```

---

## Task 2: PluginRegistryDB (SQLite persistence)

Pure I/O module, no network, no subprocess. One table, five public methods.

**Files:**
- Create: `agent/plugins/registry_db.py`
- Test: `tests/plugins/test_registry_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/plugins/test_registry_db.py`:

```python
import pytest
from agent.plugins.registry_db import PluginRegistryDB


@pytest.fixture
def db(tmp_path):
    return PluginRegistryDB(tmp_path / "plugins.db")


def test_upsert_and_get(db):
    db.upsert(
        name="demo", version="1.0.0", status="installed",
        score=85, permissions=["net.http"], install_path="/a/b",
    )
    p = db.get("demo")
    assert p is not None
    assert p.name == "demo"
    assert p.score == 85
    assert p.permissions == ["net.http"]


def test_get_missing_returns_none(db):
    assert db.get("nope") is None


def test_list_all(db):
    db.upsert(name="a", version="1", status="installed", score=80, permissions=[], install_path="/a")
    db.upsert(name="b", version="1", status="error",     score=0,  permissions=[], install_path="/b")
    rows = db.list_all()
    assert {r.name for r in rows} == {"a", "b"}


def test_delete(db):
    db.upsert(name="x", version="1", status="installed", score=80, permissions=[], install_path="/x")
    assert db.delete("x") is True
    assert db.get("x") is None
    assert db.delete("x") is False  # idempotent


def test_corrupt_db_rebuilds(tmp_path):
    path = tmp_path / "plugins.db"
    path.write_bytes(b"not a sqlite file")
    # constructor should rename to .bak and start fresh, not raise
    db = PluginRegistryDB(path)
    assert db.list_all() == []
    assert (tmp_path / "plugins.db.bak").exists()


def test_upsert_updates_existing(db):
    db.upsert(name="demo", version="1", status="installed", score=70, permissions=[], install_path="/a")
    db.upsert(name="demo", version="2", status="installed", score=90, permissions=["env"], install_path="/a")
    p = db.get("demo")
    assert p.version == "2"
    assert p.score == 90
    assert p.permissions == ["env"]
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_registry_db.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement registry_db.py**

Create `agent/plugins/registry_db.py`:

```python
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
  last_error TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PluginRegistryDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
        except sqlite3.DatabaseError:
            logger.warning("plugins.db corrupt — renaming to .bak")
            bak = self.path.with_suffix(self.path.suffix + ".bak")
            self.path.replace(bak)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)

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
    ) -> None:
        now = _utc_now()
        cur = self._conn.execute("SELECT installed_at FROM plugins WHERE name=?", (name,))
        row = cur.fetchone()
        installed_at = row["installed_at"] if row else now
        self._conn.execute(
            """
            INSERT INTO plugins(name, version, status, score, permissions,
                                install_path, installed_at, last_audited_at, last_error)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                version=excluded.version,
                status=excluded.status,
                score=excluded.score,
                permissions=excluded.permissions,
                install_path=excluded.install_path,
                last_audited_at=excluded.last_audited_at,
                last_error=excluded.last_error
            """,
            (
                name, version, status, score, json.dumps(permissions),
                install_path, installed_at, now, last_error,
            ),
        )
        self._conn.commit()

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
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_registry_db.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/agentic
git add agent/plugins/registry_db.py tests/plugins/test_registry_db.py
git commit -m "feat(plugins): SQLite registry DB with corrupt-file recovery"
```

---

## Task 3: HubScout — fetch, cache, search

Wraps the remote hub with 10-min memory cache + 7-day disk fallback. No subprocess, no write to filesystem beyond cache file.

**Files:**
- Create: `agent/plugins/hub_scout.py`
- Create: `tests/plugins/conftest.py` (shared fake-hub fixture)
- Test: `tests/plugins/test_hub_scout.py`

- [ ] **Step 1: Write the shared fake-hub fixture**

Create `tests/plugins/conftest.py`:

```python
"""Shared fixtures: a local aiohttp fake hub serving a controlled index."""
import json
import pytest
from aiohttp import web


_INDEX = {
    "version": 1,
    "plugins": [
        {
            "name": "demo",
            "version": "1.0.0",
            "url": "http://{host}/artefacts/demo-1.0.0.tar.gz",
            "sha256": "a" * 64,
            "author": "tester",
            "description": "a demo",
            "category": "utility",
            "tags": ["demo", "test"],
            "permissions": ["fs.read"],
            "tool_count": 1,
            "size_bytes": 1024,
        },
        {
            "name": "deploy-fly",
            "version": "0.2.0",
            "url": "http://{host}/artefacts/deploy-fly-0.2.0.tar.gz",
            "sha256": "b" * 64,
            "author": "ops",
            "description": "deploy to fly.io",
            "category": "devops",
            "tags": ["deploy", "flyio"],
            "permissions": ["net.http", "subprocess"],
            "tool_count": 3,
            "size_bytes": 5432,
        },
    ],
}


@pytest.fixture
async def fake_hub(aiohttp_server):
    """Start a local hub and return (url, index_dict). Artefact bytes are kept in memory."""
    artefacts: dict[str, bytes] = {}

    async def index_handler(request):
        host = request.host
        cloned = json.loads(json.dumps(_INDEX))
        for p in cloned["plugins"]:
            p["url"] = p["url"].format(host=host)
        return web.json_response(cloned)

    async def artefact_handler(request):
        name = request.match_info["name"]
        if name not in artefacts:
            return web.Response(status=404)
        return web.Response(body=artefacts[name], content_type="application/gzip")

    app = web.Application()
    app.router.add_get("/index.json", index_handler)
    app.router.add_get("/artefacts/{name}", artefact_handler)
    server = await aiohttp_server(app)
    url = f"http://{server.host}:{server.port}/index.json"
    return {"url": url, "artefacts": artefacts, "server": server}
```

- [ ] **Step 2: Write failing tests for HubScout**

Create `tests/plugins/test_hub_scout.py`:

```python
import json
import time
import pytest

from agent.plugins.hub_scout import HubScout


@pytest.mark.asyncio
async def test_fetch_index(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("")
    names = {r.name for r in results}
    assert names == {"demo", "deploy-fly"}


@pytest.mark.asyncio
async def test_search_by_name_substring(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("deploy")
    assert [r.name for r in results] == ["deploy-fly"]


@pytest.mark.asyncio
async def test_search_by_category(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("", category="devops")
    assert [r.name for r in results] == ["deploy-fly"]


@pytest.mark.asyncio
async def test_inspect_exact_match(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    m = await scout.inspect("demo")
    assert m.version == "1.0.0"
    assert m.permissions == ["fs.read"]


@pytest.mark.asyncio
async def test_inspect_missing_returns_none(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    assert await scout.inspect("does-not-exist") is None


@pytest.mark.asyncio
async def test_disk_cache_used_when_offline(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    await scout.search("")  # populate cache
    # Swap to an unreachable URL
    scout._index_url = "http://127.0.0.1:1/index.json"
    scout._mem_cache = None
    scout._mem_cache_at = 0.0
    results = await scout.search("")
    # Served from disk cache
    assert {r.name for r in results} == {"demo", "deploy-fly"}
```

- [ ] **Step 3: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_hub_scout.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement hub_scout.py**

Create `agent/plugins/hub_scout.py`:

```python
"""Hub index fetcher. 10-min memory cache, 7-day disk fallback.

Designed so that network failure never blocks the user — a stale cache is
preferred to a hard error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

from agent.plugins.types import PluginMeta

logger = logging.getLogger(__name__)

_MEM_TTL_S = 600              # 10 minutes
_DISK_MAX_STALE_S = 7 * 86400 # 7 days


class HubScout:
    def __init__(
        self,
        index_url: str,
        cache_dir: str | Path,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._index_url = index_url
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "index.cache.json"
        self._timeout_s = timeout_s
        self._mem_cache: list[PluginMeta] | None = None
        self._mem_cache_at: float = 0.0
        self._lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
    ) -> list[PluginMeta]:
        index = await self._get_index()
        q = query.lower().strip()
        out: list[PluginMeta] = []
        for p in index:
            if category and p.category != category:
                continue
            if q and q not in p.name.lower() and q not in p.description.lower():
                continue
            out.append(p)
        return out

    async def inspect(self, name: str) -> PluginMeta | None:
        index = await self._get_index()
        for p in index:
            if p.name == name:
                return p
        return None

    async def _get_index(self) -> list[PluginMeta]:
        async with self._lock:
            now = time.time()
            if self._mem_cache is not None and (now - self._mem_cache_at) < _MEM_TTL_S:
                return self._mem_cache

            try:
                data = await self._fetch_remote()
                self._write_disk_cache(data)
                self._mem_cache = self._parse(data)
                self._mem_cache_at = now
                return self._mem_cache
            except Exception as e:
                logger.warning("Hub fetch failed (%s); falling back to disk cache", e)
                data = self._read_disk_cache()
                if data is None:
                    raise
                self._mem_cache = self._parse(data)
                self._mem_cache_at = now
                return self._mem_cache

    async def _fetch_remote(self) -> dict:
        timeout = aiohttp.ClientTimeout(total=self._timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self._index_url) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _read_disk_cache(self) -> dict | None:
        if not self._cache_file.exists():
            return None
        age = time.time() - self._cache_file.stat().st_mtime
        if age > _DISK_MAX_STALE_S:
            return None
        try:
            return json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_disk_cache(self, data: dict) -> None:
        try:
            self._cache_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            logger.debug("Hub cache write failed: %s", e)

    @staticmethod
    def _parse(data: dict) -> list[PluginMeta]:
        out: list[PluginMeta] = []
        for p in data.get("plugins", []):
            out.append(
                PluginMeta(
                    name=p["name"],
                    version=p["version"],
                    url=p["url"],
                    sha256=p["sha256"],
                    author=p.get("author", ""),
                    description=p.get("description", ""),
                    category=p.get("category", ""),
                    tags=p.get("tags", []),
                    permissions=p.get("permissions", []),
                    tool_count=p.get("tool_count", 0),
                    size_bytes=p.get("size_bytes", 0),
                    signature=p.get("signature"),
                )
            )
        return out
```

- [ ] **Step 5: Install test deps if missing**

Run: `pip install pytest-aiohttp pytest-asyncio 2>&1 | tail -3`
(Already installed in most setups — skip if "Requirement already satisfied".)

- [ ] **Step 6: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_hub_scout.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
cd D:/agentic
git add agent/plugins/hub_scout.py tests/plugins/conftest.py tests/plugins/test_hub_scout.py
git commit -m "feat(plugins): HubScout with 10-min memory + 7-day disk cache"
```

---

## Task 4: Installer — download, verify SHA256, atomic promote

Downloads a tarball to a temp dir, verifies hash, extracts with path-traversal guard, promotes via `os.replace`. Never executes plugin code.

**Files:**
- Create: `agent/plugins/installer.py`
- Test: `tests/plugins/test_installer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/plugins/test_installer.py`:

```python
import hashlib
import io
import tarfile
import pytest

from agent.plugins.installer import Installer, IntegrityError, BadArchiveError


def _make_tarball(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.mark.asyncio
async def test_download_and_promote(tmp_path, fake_hub):
    blob = _make_tarball({
        "demo/plugin.json": b'{"name":"demo","version":"1.0.0","tools":[],"permissions":[]}',
        "demo/tools.py": b"__skill_tools__ = []\n",
    })
    fake_hub["artefacts"]["demo-1.0.0.tar.gz"] = blob
    digest = _sha256(blob)

    install_root = tmp_path / "plugins"
    inst = Installer(install_root=install_root, temp_root=tmp_path / "tmp")

    from agent.plugins.types import PluginMeta
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="demo", version="1.0.0",
        url=f"http://{host}:{port}/artefacts/demo-1.0.0.tar.gz",
        sha256=digest,
    )
    staged = await inst.download_and_extract(meta)
    assert (staged / "demo" / "plugin.json").is_file()

    final = inst.promote(staged, name="demo", version="1.0.0")
    assert final.is_dir()
    assert (final / "demo" / "plugin.json").is_file()


@pytest.mark.asyncio
async def test_sha_mismatch_raises(tmp_path, fake_hub):
    blob = _make_tarball({"a.py": b"x=1"})
    fake_hub["artefacts"]["bad.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="bad", version="1", sha256="0" * 64,
        url=f"http://{host}:{port}/artefacts/bad.tar.gz",
    )
    with pytest.raises(IntegrityError):
        await inst.download_and_extract(meta)


@pytest.mark.asyncio
async def test_rejects_path_traversal_member(tmp_path, fake_hub):
    blob = _make_tarball({"../../etc/evil": b"x"})
    fake_hub["artefacts"]["evil.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="evil", version="1", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/evil.tar.gz",
    )
    with pytest.raises(BadArchiveError):
        await inst.download_and_extract(meta)


@pytest.mark.asyncio
async def test_rejects_absolute_path_member(tmp_path, fake_hub):
    blob = _make_tarball({"/etc/evil": b"x"})
    fake_hub["artefacts"]["abs.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="abs", version="1", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/abs.tar.gz",
    )
    with pytest.raises(BadArchiveError):
        await inst.download_and_extract(meta)


def test_promote_idempotent_replace(tmp_path):
    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    # Prepare two staged dirs for the same plugin
    s1 = tmp_path / "s1"
    (s1 / "a").mkdir(parents=True)
    (s1 / "a" / "x.txt").write_text("v1")
    s2 = tmp_path / "s2"
    (s2 / "a").mkdir(parents=True)
    (s2 / "a" / "x.txt").write_text("v2")

    final1 = inst.promote(s1, name="p", version="1")
    assert (final1 / "a" / "x.txt").read_text() == "v1"
    final2 = inst.promote(s2, name="p", version="2")
    assert (final2 / "a" / "x.txt").read_text() == "v2"
    # Old version directory is gone
    assert not final1.exists() or final1 == final2
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_installer.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement installer.py**

Create `agent/plugins/installer.py`:

```python
"""Installer — download, SHA256 verify, safe extract, atomic promote.

Never imports plugin code. Hard-blocks path traversal or absolute paths
in tar members.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path

import aiohttp

from agent.plugins.types import PluginMeta

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_S = 60
_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024   # 50 MB hard cap


class InstallerError(Exception):
    """Base class for installer errors."""


class IntegrityError(InstallerError):
    """SHA256 mismatch or signature failure."""


class BadArchiveError(InstallerError):
    """Tarball contains a path-traversal or absolute-path member."""


class Installer:
    def __init__(self, install_root: str | Path, temp_root: str | Path) -> None:
        self.install_root = Path(install_root)
        self.temp_root = Path(temp_root)
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    async def download_and_extract(self, meta: PluginMeta) -> Path:
        """Download tarball, verify SHA256, extract to a temp dir.
        Returns the staging path. Raises IntegrityError or BadArchiveError.
        Cleans up on failure."""
        stage = self.temp_root / f"pending-{uuid.uuid4().hex[:8]}"
        stage.mkdir(parents=True, exist_ok=True)
        try:
            blob = await self._download(meta.url)
            actual = hashlib.sha256(blob).hexdigest()
            if actual != meta.sha256:
                raise IntegrityError(
                    f"SHA256 mismatch: expected {meta.sha256[:12]}…, got {actual[:12]}…"
                )
            self._safe_extract(blob, stage)
            return stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def promote(self, stage: Path, *, name: str, version: str) -> Path:
        """Move the staged dir to its final install path atomically.
        Replaces any existing installation of the same plugin.
        """
        final = self.install_root / f"{name}-{version}"
        # If an older version exists for this name, remove it first
        for child in self.install_root.iterdir():
            if child.name.startswith(f"{name}-") and child != final:
                shutil.rmtree(child, ignore_errors=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(stage, final)
        return final

    async def _download(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                length = int(resp.headers.get("Content-Length", "0"))
                if length and length > _MAX_ARTIFACT_BYTES:
                    raise InstallerError(f"artifact too large: {length} bytes")
                data = await resp.read()
                if len(data) > _MAX_ARTIFACT_BYTES:
                    raise InstallerError(f"artifact too large: {len(data)} bytes")
                return data

    @staticmethod
    def _safe_extract(blob: bytes, dest: Path) -> None:
        import io
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise BadArchiveError(f"unsafe member: {member.name!r}")
                if member.islnk() or member.issym():
                    raise BadArchiveError(f"symlink rejected: {member.name!r}")
            tar.extractall(dest)
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_installer.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/agentic
git add agent/plugins/installer.py tests/plugins/test_installer.py
git commit -m "feat(plugins): Installer with SHA256 + safe tarball extraction"
```

---

## Task 5: QualityAuditor — reuse QualityIntel + manifest + AST checks

Runs the existing ruff/mypy/bandit scanners over the plugin dir, plus adds manifest validation and top-level side-effect AST scan. Produces a `QualityReport`.

**Files:**
- Create: `agent/plugins/auditor.py`
- Create: `tests/plugins/fixtures/good_plugin/` (benign)
- Create: `tests/plugins/fixtures/eval_plugin/` (module-level eval — should block)
- Test: `tests/plugins/test_auditor.py`

- [ ] **Step 1: Build fixtures**

Create `tests/plugins/fixtures/good_plugin/plugin.json`:

```json
{
  "name": "good",
  "version": "1.0.0",
  "tools": ["say_hi"],
  "permissions": ["net.http"],
  "entry": "good.tools"
}
```

Create `tests/plugins/fixtures/good_plugin/good/__init__.py`: (empty)

Create `tests/plugins/fixtures/good_plugin/good/tools.py`:

```python
from langchain_core.tools import tool


@tool
def say_hi(name: str) -> str:
    """Say hi."""
    return f"hi {name}"


__skill_tools__ = [say_hi]
```

Create `tests/plugins/fixtures/eval_plugin/plugin.json`:

```json
{
  "name": "evalbad",
  "version": "1.0.0",
  "tools": ["t"],
  "permissions": [],
  "entry": "evalbad.tools"
}
```

Create `tests/plugins/fixtures/eval_plugin/evalbad/__init__.py`: (empty)

Create `tests/plugins/fixtures/eval_plugin/evalbad/tools.py`:

```python
import os
eval("1+1")               # top-level eval — must block
os.system("echo hi")      # top-level subprocess — must block

from langchain_core.tools import tool


@tool
def t() -> str:
    return "x"


__skill_tools__ = [t]
```

- [ ] **Step 2: Write failing tests**

Create `tests/plugins/test_auditor.py`:

```python
from pathlib import Path
import pytest

from agent.plugins.auditor import QualityAuditor

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_good_plugin_passes():
    rep = await QualityAuditor().audit(FIX / "good_plugin")
    assert rep.blocked is False
    assert rep.score >= 60


@pytest.mark.asyncio
async def test_eval_plugin_blocked():
    rep = await QualityAuditor().audit(FIX / "eval_plugin")
    assert rep.blocked is True
    rules = {b.rule for b in rep.blockers}
    assert "top-level-side-effect" in rules


@pytest.mark.asyncio
async def test_missing_manifest_blocked(tmp_path):
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / "x.py").write_text("__skill_tools__ = []\n")
    rep = await QualityAuditor().audit(tmp_path / "plugin")
    assert rep.blocked is True
    assert any(b.rule == "missing-manifest" for b in rep.blockers)


@pytest.mark.asyncio
async def test_unknown_permission_blocked(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "plugin.json").write_text(
        '{"name":"x","version":"1","tools":[],"permissions":["root.access"],"entry":"x"}'
    )
    (tmp_path / "p" / "x.py").write_text("__skill_tools__ = []\n")
    rep = await QualityAuditor().audit(tmp_path / "p")
    assert rep.blocked is True
    assert any(b.rule == "unknown-permission" for b in rep.blockers)
```

- [ ] **Step 3: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_auditor.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement auditor.py**

Create `agent/plugins/auditor.py`:

```python
"""QualityAuditor — static analysis over a plugin source dir.

Reuses ruff / mypy / bandit (same tools QualityIntel uses) plus three
plugin-specific checks:
  1. plugin.json manifest is present and valid
  2. Permissions are in the known vocabulary
  3. No top-level side effects (eval/exec/open/requests/subprocess at module level)
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
from pathlib import Path

from agent.plugins.types import QualityIssue, QualityReport

logger = logging.getLogger(__name__)

_ALLOWED_PERMS = {"fs.read", "fs.write", "net.http", "subprocess", "env"}
_SEV_WEIGHT = {"high": 10, "medium": 3, "low": 1}

_SIDE_EFFECT_CALLS = {
    "eval", "exec", "compile",
    "open",
    "os.system", "os.popen",
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "urllib.request.urlopen",
}


class QualityAuditor:
    async def audit(self, plugin_dir: str | Path) -> QualityReport:
        pdir = Path(plugin_dir)
        issues: list[QualityIssue] = []
        blockers: list[QualityIssue] = []
        raw: dict = {}

        # 1. Manifest
        manifest = pdir / "plugin.json"
        if not manifest.is_file():
            blockers.append(QualityIssue(
                rule="missing-manifest",
                message="plugin.json not found",
                severity="high",
            ))
            return QualityReport(score=0, issues=issues, blockers=blockers, raw=raw)

        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            blockers.append(QualityIssue(
                rule="invalid-manifest",
                message=f"plugin.json is not valid JSON: {e}",
                severity="high",
            ))
            return QualityReport(score=0, issues=issues, blockers=blockers, raw=raw)

        # Required fields
        for field in ("name", "version", "tools", "permissions"):
            if field not in data:
                blockers.append(QualityIssue(
                    rule="invalid-manifest",
                    message=f"plugin.json missing field '{field}'",
                    severity="high",
                ))

        # 2. Permissions vocabulary
        for p in data.get("permissions", []):
            base = p.split("=")[0] if isinstance(p, str) else str(p)
            if base not in _ALLOWED_PERMS:
                blockers.append(QualityIssue(
                    rule="unknown-permission",
                    message=f"unknown permission '{p}' (allowed: {sorted(_ALLOWED_PERMS)})",
                    severity="high",
                ))

        # 3. Top-level side effects
        for py in pdir.rglob("*.py"):
            try:
                src = py.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src, filename=str(py))
            except (OSError, SyntaxError) as e:
                issues.append(QualityIssue(
                    rule="parse-error", message=str(e), severity="low",
                    file=str(py.relative_to(pdir)),
                ))
                continue
            for node in tree.body:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    name = _call_name(node.value)
                    if name in _SIDE_EFFECT_CALLS:
                        blockers.append(QualityIssue(
                            rule="top-level-side-effect",
                            message=f"top-level call to {name}() at import time",
                            severity="high",
                            file=str(py.relative_to(pdir)),
                            line=node.lineno,
                        ))

        # 4. Static analysers (best-effort — skip silently if missing)
        raw["ruff"] = await self._run_if_present("ruff", ["check", "--output-format", "json", str(pdir)])
        raw["bandit"] = await self._run_if_present("bandit", ["-r", str(pdir), "-f", "json", "-q"])
        raw["mypy"] = await self._run_if_present("mypy", ["--no-error-summary", "--hide-error-context", str(pdir)])

        self._issues_from_ruff(raw.get("ruff", {}), issues)
        self._issues_from_bandit(raw.get("bandit", {}), issues, blockers)

        # Score: start at 100, subtract severity penalties
        penalty = sum(_SEV_WEIGHT[i.severity] for i in issues)
        penalty += sum(_SEV_WEIGHT[b.severity] for b in blockers)
        score = max(0, 100 - penalty)
        return QualityReport(score=score, issues=issues, blockers=blockers, raw=raw)

    @staticmethod
    async def _run_if_present(tool: str, args: list[str]) -> dict:
        if shutil.which(tool) is None:
            return {}
        try:
            proc = await asyncio.create_subprocess_exec(
                tool, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            return {"exit": proc.returncode, "stdout": stdout_b.decode("utf-8", "replace")}
        except (asyncio.TimeoutError, OSError) as e:
            return {"error": str(e)}

    @staticmethod
    def _issues_from_ruff(raw: dict, issues: list[QualityIssue]) -> None:
        out = raw.get("stdout", "")
        if not out.strip():
            return
        try:
            arr = json.loads(out)
        except json.JSONDecodeError:
            return
        for item in arr[:50]:
            issues.append(QualityIssue(
                rule=item.get("code", "RUFF"),
                message=item.get("message", "")[:200],
                severity="low",
                file=item.get("filename", ""),
                line=(item.get("location") or {}).get("row", 0),
            ))

    @staticmethod
    def _issues_from_bandit(raw: dict, issues: list[QualityIssue], blockers: list[QualityIssue]) -> None:
        out = raw.get("stdout", "")
        if not out.strip():
            return
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return
        for item in (data.get("results") or [])[:50]:
            sev = (item.get("issue_severity") or "LOW").lower()
            msg = item.get("issue_text", "")[:200]
            iss = QualityIssue(
                rule=item.get("test_id", "B000"),
                message=msg,
                severity=sev if sev in {"high", "medium", "low"} else "low",
                file=item.get("filename", ""),
                line=item.get("line_number", 0),
            )
            if sev == "high":
                blockers.append(iss)
            else:
                issues.append(iss)


def _call_name(node: ast.Call) -> str:
    """Extract dotted-path name of an ast.Call's target."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""
```

- [ ] **Step 5: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_auditor.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add agent/plugins/auditor.py tests/plugins/test_auditor.py tests/plugins/fixtures/
git commit -m "feat(plugins): QualityAuditor — manifest + AST + ruff/bandit"
```

---

## Task 6: plugin_host.py — subprocess-side JSON-RPC server

The binary each plugin subprocess runs. It installs permission gates, imports the plugin module, enumerates `__skill_tools__`, and serves `tool.list` / `tool.invoke` / `shutdown` over length-prefixed JSON-RPC 2.0 on stdin/stdout.

**Files:**
- Create: `shadowdev/plugin_host.py`
- Test: `tests/plugins/test_rpc_contract.py` (runs the binary in a real subprocess)

- [ ] **Step 1: Write failing tests**

Create `tests/plugins/test_rpc_contract.py`:

```python
"""Tests that exercise the plugin_host binary via a real subprocess.

Uses the good_plugin fixture. Communicates via length-prefixed JSON-RPC.
"""
import asyncio
import json
import struct
import sys
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


async def _rpc_send(writer: asyncio.StreamWriter, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


async def _rpc_recv(reader: asyncio.StreamReader) -> dict:
    hdr = await reader.readexactly(4)
    (n,) = struct.unpack(">I", hdr)
    body = await reader.readexactly(n)
    return json.loads(body.decode("utf-8"))


async def _spawn_host(plugin_dir: Path, perms: list[str]):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "shadowdev.plugin_host",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),  # project root
    )
    # Send handshake
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 0, "method": "handshake",
        "params": {"plugin_dir": str(plugin_dir), "permissions": perms},
    })
    ack = await _rpc_recv(proc.stdout)
    assert ack.get("result", {}).get("ok") is True, ack
    return proc


@pytest.mark.asyncio
async def test_list_tools():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {"jsonrpc": "2.0", "id": 1, "method": "tool.list"})
    resp = await _rpc_recv(proc.stdout)
    assert "result" in resp
    names = {t["name"] for t in resp["result"]}
    assert "say_hi" in names
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_tool_invoke_success():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 2, "method": "tool.invoke",
        "params": {"name": "say_hi", "args": {"name": "world"}},
    })
    resp = await _rpc_recv(proc.stdout)
    assert resp.get("result") == "hi world"
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_invoke_unknown_tool_returns_error():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 3, "method": "tool.invoke",
        "params": {"name": "nope", "args": {}},
    })
    resp = await _rpc_recv(proc.stdout)
    assert "error" in resp
    assert resp["error"]["code"] == -32601
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_shutdown_clean_exit():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {"jsonrpc": "2.0", "id": 4, "method": "shutdown"})
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    assert proc.returncode == 0
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_rpc_contract.py -v`
Expected: FAIL — module `shadowdev.plugin_host` not found.

- [ ] **Step 3: Implement plugin_host.py**

Create `shadowdev/plugin_host.py`:

```python
"""Plugin host — runs inside each plugin's subprocess.

Protocol: length-prefixed JSON-RPC 2.0 over stdin/stdout.
Framing: 4-byte big-endian length, then JSON body (UTF-8).

Startup sequence:
  1. Receive handshake {method: "handshake", params: {plugin_dir, permissions}}
  2. Install monkey-patches for disallowed capabilities
  3. Import plugin module, enumerate __skill_tools__
  4. Reply {result: {ok: true, tools: [...]}}
  5. Serve tool.list / tool.invoke / shutdown in a loop

Exit codes:
  0 — clean shutdown
  1 — import / handshake error
  2 — fatal RPC error
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import io
import json
import os
import socket
import struct
import subprocess as _subprocess
import sys
import traceback
from pathlib import Path


class PermissionDenied(PermissionError):
    pass


_ALLOW_NET: list[str] = []     # list of allowed hostnames (case-insensitive)
_ALLOW_FS_READ: list[str] = []  # list of allowed real-path roots
_ALLOW_FS_WRITE: list[str] = []
_ALLOW_SUBPROCESS: bool = False
_ALLOW_ENV: bool = False


def _install_gates(perms: list[str]) -> None:
    """Parse permission strings and install runtime gates.

    Permission grammar:
      fs.read | fs.write            — global allow (rarely used)
      fs.read=[path1,path2]         — path-restricted
      net.http | net.http=[host,*]  — allow all or allowlisted hosts
      subprocess                    — allow subprocess module
      env                           — allow reading env vars
    """
    global _ALLOW_SUBPROCESS, _ALLOW_ENV
    for p in perms:
        if p == "subprocess":
            _ALLOW_SUBPROCESS = True
        elif p == "env":
            _ALLOW_ENV = True
        elif p.startswith("net.http"):
            if "=" in p:
                _ALLOW_NET.extend(_parse_list(p.split("=", 1)[1]))
            else:
                _ALLOW_NET.append("*")
        elif p.startswith("fs.read"):
            if "=" in p:
                _ALLOW_FS_READ.extend(_resolve_roots(p.split("=", 1)[1]))
            else:
                _ALLOW_FS_READ.append("/")
        elif p.startswith("fs.write"):
            if "=" in p:
                _ALLOW_FS_WRITE.extend(_resolve_roots(p.split("=", 1)[1]))
            else:
                _ALLOW_FS_WRITE.append("/")

    # --- socket.connect ---
    orig_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else ""
        if "*" not in _ALLOW_NET and host.lower() not in (h.lower() for h in _ALLOW_NET):
            raise PermissionDenied(f"denied: net.http ({host})")
        return orig_connect(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]

    # --- builtins.open ---
    import builtins
    orig_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        path = os.fspath(file)
        try:
            real = Path(path).resolve()
        except OSError:
            real = Path(path)
        is_write = any(c in mode for c in ("w", "a", "x", "+"))
        roots = _ALLOW_FS_WRITE if is_write else _ALLOW_FS_READ
        allowed = any(_is_under(real, Path(r).resolve()) for r in roots) if roots else False
        if not allowed:
            raise PermissionDenied(f"denied: fs.{'write' if is_write else 'read'} ({real})")
        return orig_open(file, mode, *args, **kwargs)

    builtins.open = guarded_open  # type: ignore[assignment]

    # --- subprocess.Popen ---
    orig_popen = _subprocess.Popen

    class GuardedPopen(orig_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            if not _ALLOW_SUBPROCESS:
                raise PermissionDenied("denied: subprocess")
            super().__init__(*args, **kwargs)

    _subprocess.Popen = GuardedPopen  # type: ignore[assignment]


def _parse_list(s: str) -> list[str]:
    s = s.strip().lstrip("[").rstrip("]")
    return [item.strip() for item in s.split(",") if item.strip()]


def _resolve_roots(s: str) -> list[str]:
    return [os.path.expanduser(r) for r in _parse_list(s)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ─── Plugin loader ───────────────────────────────────────────────

def _load_plugin(plugin_dir: str) -> dict:
    """Load the plugin's entry module and return {name: tool}."""
    pdir = Path(plugin_dir)
    manifest = json.loads((pdir / "plugin.json").read_text(encoding="utf-8"))
    entry = manifest["entry"]  # e.g. "good.tools"

    sys.path.insert(0, str(pdir))
    module = importlib.import_module(entry)
    tools = {}
    for t in getattr(module, "__skill_tools__", []):
        name = getattr(t, "name", getattr(t, "__name__", None))
        if name:
            tools[name] = t
    return tools


# ─── Framing ─────────────────────────────────────────────────────

def _read_frame() -> dict | None:
    hdr = sys.stdin.buffer.read(4)
    if not hdr or len(hdr) < 4:
        return None
    (n,) = struct.unpack(">I", hdr)
    body = sys.stdin.buffer.read(n)
    if len(body) < n:
        return None
    return json.loads(body.decode("utf-8"))


def _write_frame(obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack(">I", len(data)) + data)
    sys.stdout.buffer.flush()


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main() -> int:
    # 1. Handshake
    req = _read_frame()
    if req is None or req.get("method") != "handshake":
        _write_frame(_err(None, -32600, "expected handshake"))
        return 1
    rid = req.get("id")
    params = req.get("params") or {}
    try:
        _install_gates(params.get("permissions") or [])
        tools = _load_plugin(params["plugin_dir"])
    except Exception as e:
        _write_frame(_err(rid, -32000, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
        return 1
    _write_frame(_ok(rid, {"ok": True, "tools": [{"name": n} for n in tools]}))

    # 2. Serve loop
    while True:
        req = _read_frame()
        if req is None:
            return 0
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        try:
            if method == "tool.list":
                _write_frame(_ok(rid, [{"name": n} for n in tools]))
            elif method == "tool.invoke":
                name = params.get("name")
                args = params.get("args") or {}
                if name not in tools:
                    _write_frame(_err(rid, -32601, f"unknown tool: {name}"))
                    continue
                try:
                    result = tools[name].invoke(args)
                except PermissionDenied as pe:
                    _write_frame(_err(rid, -32001, str(pe)))
                    continue
                _write_frame(_ok(rid, result))
            elif method == "shutdown":
                _write_frame(_ok(rid, {"ok": True}))
                return 0
            else:
                _write_frame(_err(rid, -32601, f"unknown method: {method}"))
        except Exception as e:
            _write_frame(_err(rid, -32000, f"{type(e).__name__}: {e}"))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_rpc_contract.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/agentic
git add shadowdev/plugin_host.py tests/plugins/test_rpc_contract.py
git commit -m "feat(plugins): plugin_host — JSON-RPC subprocess + permission gates"
```

---

## Task 7: RuntimeSandbox + ProxyTool (host side)

Host-side counterpart to plugin_host. Spawns the subprocess, does the handshake, exposes LangChain-compatible `ProxyTool` instances that translate `.invoke()` into RPC calls.

**Files:**
- Create: `agent/plugins/sandbox.py`
- Test: `tests/plugins/test_sandbox_boundaries.py`

- [ ] **Step 1: Add hostile fixture**

Create `tests/plugins/fixtures/hostile_plugin/plugin.json`:

```json
{"name":"hostile","version":"1.0.0","tools":["bad_net","bad_fs","bad_sub","slow"],"permissions":[],"entry":"hostile.tools"}
```

Create `tests/plugins/fixtures/hostile_plugin/hostile/__init__.py`: (empty)

Create `tests/plugins/fixtures/hostile_plugin/hostile/tools.py`:

```python
import time
import socket
import subprocess
from langchain_core.tools import tool


@tool
def bad_net() -> str:
    """Try to open a socket — must be denied unless net.http granted."""
    s = socket.socket()
    s.connect(("example.com", 80))
    return "connected"


@tool
def bad_fs() -> str:
    """Try to read /etc/passwd — must be denied unless fs.read granted."""
    with open("/etc/passwd") as f:
        return f.read()[:10]


@tool
def bad_sub() -> str:
    """Try to run echo — must be denied unless subprocess granted."""
    subprocess.run(["echo", "hi"], check=True)
    return "ran"


@tool
def slow() -> str:
    """Sleep 60s — sandbox should time out at 30s."""
    time.sleep(60)
    return "done"


__skill_tools__ = [bad_net, bad_fs, bad_sub, slow]
```

- [ ] **Step 2: Write failing tests**

Create `tests/plugins/test_sandbox_boundaries.py`:

```python
from pathlib import Path
import pytest

from agent.plugins.sandbox import RuntimeSandbox

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_network_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_net", {})
        assert "net.http" in str(ei.value).lower() or "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_fs_read_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_fs", {})
        assert "fs.read" in str(ei.value).lower() or "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_subprocess_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception):
            await sb.invoke("bad_sub", {})
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_timeout_kills_subprocess():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[], call_timeout_s=2.0)
    await sb.start()
    try:
        with pytest.raises(TimeoutError):
            await sb.invoke("slow", {})
    finally:
        await sb.stop()
    # Subprocess should be dead
    assert sb._proc is None or sb._proc.returncode is not None


@pytest.mark.asyncio
async def test_good_plugin_list_and_invoke():
    sb = RuntimeSandbox(plugin_dir=FIX / "good_plugin", permissions=[])
    await sb.start()
    try:
        tools = sb.tool_names()
        assert "say_hi" in tools
        result = await sb.invoke("say_hi", {"name": "abc"})
        assert result == "hi abc"
    finally:
        await sb.stop()
```

- [ ] **Step 3: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_sandbox_boundaries.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement sandbox.py**

Create `agent/plugins/sandbox.py`:

```python
"""RuntimeSandbox — host side of the plugin subprocess protocol.

Spawns `python -m shadowdev.plugin_host`, performs handshake, exposes a simple
async API: start / stop / invoke / tool_names.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxError(RuntimeError):
    pass


class RuntimeSandbox:
    def __init__(
        self,
        plugin_dir: str | Path,
        permissions: list[str],
        *,
        call_timeout_s: float = 30.0,
    ) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.permissions = list(permissions)
        self.call_timeout_s = call_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._tools: list[str] = []
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "shadowdev.plugin_host",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ack = await self._rpc("handshake", {
            "plugin_dir": str(self.plugin_dir),
            "permissions": self.permissions,
        })
        self._tools = [t["name"] for t in ack.get("tools", [])]

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            await asyncio.wait_for(self._rpc("shutdown", {}), timeout=2.0)
        except Exception:
            pass
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    def tool_names(self) -> list[str]:
        return list(self._tools)

    async def invoke(self, name: str, args: dict):
        try:
            return await asyncio.wait_for(
                self._rpc("tool.invoke", {"name": name, "args": args}),
                timeout=self.call_timeout_s,
            )
        except asyncio.TimeoutError:
            # Kill the subprocess so it doesn't stay hung
            if self._proc and self._proc.returncode is None:
                self._proc.kill()
                await self._proc.wait()
            self._proc = None
            raise TimeoutError(
                f"plugin call {name!r} exceeded {self.call_timeout_s}s timeout"
            )

    async def _rpc(self, method: str, params: dict):
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        async with self._lock:
            rid = self._next_id
            self._next_id += 1
            body = json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "method": method, "params": params,
            }).encode("utf-8")
            self._proc.stdin.write(struct.pack(">I", len(body)) + body)
            await self._proc.stdin.drain()

            hdr = await self._proc.stdout.readexactly(4)
            (n,) = struct.unpack(">I", hdr)
            body = await self._proc.stdout.readexactly(n)
            reply = json.loads(body.decode("utf-8"))
            if "error" in reply:
                err = reply["error"]
                raise SandboxError(f"[{err['code']}] {err['message']}")
            return reply.get("result")
```

- [ ] **Step 5: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_sandbox_boundaries.py -v`
Expected: 5 passed. (Note: on Windows `/etc/passwd` test still passes because *any* path not in an allowlist is denied.)

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add agent/plugins/sandbox.py tests/plugins/test_sandbox_boundaries.py tests/plugins/fixtures/hostile_plugin/
git commit -m "feat(plugins): RuntimeSandbox with timeout + permission-gated RPC"
```

---

## Task 8: PluginManager facade + end-to-end integration test

Glues HubScout + Installer + Auditor + Sandbox + RegistryDB into one class. The rest of the app only imports this.

**Files:**
- Create: `agent/plugins/manager.py`
- Test: `tests/plugins/test_manager_integration.py`

- [ ] **Step 1: Write failing integration test (end-to-end)**

Create `tests/plugins/test_manager_integration.py`:

```python
import hashlib
import io
import tarfile
import pytest

from agent.plugins.manager import PluginManager


def _make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_GOOD_MANIFEST = b'{"name":"demo","version":"1.0.0","tools":["say_hi"],"permissions":[],"entry":"demo.tools"}'
_GOOD_TOOLS = b'''from langchain_core.tools import tool

@tool
def say_hi(name: str) -> str:
    """Say hi."""
    return f"hi {name}"

__skill_tools__ = [say_hi]
'''


@pytest.mark.asyncio
async def test_happy_path_install_load_invoke(tmp_path, fake_hub):
    blob = _make_tar({
        "plugin.json": _GOOD_MANIFEST,
        "demo/__init__.py": b"",
        "demo/tools.py": _GOOD_TOOLS,
    })
    fake_hub["artefacts"]["demo-1.0.0.tar.gz"] = blob
    # Patch index so sha256 matches our blob
    from agent.plugins.hub_scout import HubScout
    orig_parse = HubScout._parse
    def patched_parse(data):
        metas = orig_parse(data)
        for m in metas:
            if m.name == "demo":
                m.sha256 = _sha(blob)
        return metas
    HubScout._parse = staticmethod(patched_parse)  # type: ignore[assignment]

    try:
        mgr = PluginManager(
            hub_index_url=fake_hub["url"],
            install_root=tmp_path / "plugins",
            temp_root=tmp_path / "tmp",
            db_path=tmp_path / "plugins.db",
            cache_dir=tmp_path / "cache",
        )

        report = await mgr.audit("demo")
        assert not report.blocked

        installed = await mgr.install("demo", version="1.0.0", permissions=[])
        assert installed.name == "demo"
        assert installed.status == "installed"

        tools = await mgr.load_runtime("demo")
        assert any(t.name == "say_hi" for t in tools)

        result = await tools[0].ainvoke({"name": "abc"})
        assert result == "hi abc"

        await mgr.unload("demo")
    finally:
        HubScout._parse = staticmethod(orig_parse)  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_audit_blocker_halts_install(tmp_path, fake_hub):
    bad_tools = b"import os\nos.system('echo hi')\n__skill_tools__ = []\n"
    blob = _make_tar({
        "plugin.json": b'{"name":"bad","version":"1.0.0","tools":[],"permissions":[],"entry":"bad.tools"}',
        "bad/__init__.py": b"",
        "bad/tools.py": bad_tools,
    })
    fake_hub["artefacts"]["bad-1.0.0.tar.gz"] = blob
    # Inject a plugin entry for "bad"
    from agent.plugins.hub_scout import HubScout
    orig_parse = HubScout._parse
    def patched_parse(data):
        metas = orig_parse(data)
        from agent.plugins.types import PluginMeta
        host = fake_hub["server"].host
        port = fake_hub["server"].port
        metas.append(PluginMeta(
            name="bad", version="1.0.0",
            url=f"http://{host}:{port}/artefacts/bad-1.0.0.tar.gz",
            sha256=_sha(blob), permissions=[],
        ))
        return metas
    HubScout._parse = staticmethod(patched_parse)  # type: ignore[assignment]
    try:
        mgr = PluginManager(
            hub_index_url=fake_hub["url"],
            install_root=tmp_path / "plugins",
            temp_root=tmp_path / "tmp",
            db_path=tmp_path / "plugins.db",
            cache_dir=tmp_path / "cache",
        )
        with pytest.raises(Exception) as ei:
            await mgr.install("bad", version="1.0.0", permissions=[])
        assert "block" in str(ei.value).lower() or "audit" in str(ei.value).lower()
        assert mgr.registry.get("bad") is None
    finally:
        HubScout._parse = staticmethod(orig_parse)  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_uninstall_removes_row_and_files(tmp_path, fake_hub):
    # Pre-populate registry + install dir
    blob = _make_tar({
        "plugin.json": _GOOD_MANIFEST,
        "demo/__init__.py": b"",
        "demo/tools.py": _GOOD_TOOLS,
    })
    (tmp_path / "plugins" / "demo-1.0.0").mkdir(parents=True)
    import tarfile as _t
    with _t.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
        tar.extractall(tmp_path / "plugins" / "demo-1.0.0")

    mgr = PluginManager(
        hub_index_url=fake_hub["url"],
        install_root=tmp_path / "plugins",
        temp_root=tmp_path / "tmp",
        db_path=tmp_path / "plugins.db",
        cache_dir=tmp_path / "cache",
    )
    mgr.registry.upsert(
        name="demo", version="1.0.0", status="installed", score=90,
        permissions=[], install_path=str(tmp_path / "plugins" / "demo-1.0.0"),
    )

    await mgr.uninstall("demo")
    assert mgr.registry.get("demo") is None
    assert not (tmp_path / "plugins" / "demo-1.0.0").exists()
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_manager_integration.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement manager.py**

Create `agent/plugins/manager.py`:

```python
"""PluginManager — public facade for the plugin system.

Everything outside agent/plugins/ imports only this class.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Callable, Awaitable

from langchain_core.tools import StructuredTool

from agent.plugins.auditor import QualityAuditor
from agent.plugins.hub_scout import HubScout
from agent.plugins.installer import Installer
from agent.plugins.registry_db import PluginRegistryDB
from agent.plugins.sandbox import RuntimeSandbox
from agent.plugins.types import InstalledPlugin, PluginMeta, QualityReport

logger = logging.getLogger(__name__)


class PluginError(RuntimeError):
    pass


class PluginManager:
    def __init__(
        self,
        *,
        hub_index_url: str,
        install_root: str | Path,
        temp_root: str | Path,
        db_path: str | Path,
        cache_dir: str | Path,
    ) -> None:
        self.hub = HubScout(index_url=hub_index_url, cache_dir=cache_dir)
        self.installer = Installer(install_root=install_root, temp_root=temp_root)
        self.auditor = QualityAuditor()
        self.registry = PluginRegistryDB(db_path)
        self._sandboxes: dict[str, RuntimeSandbox] = {}

    # ─── Public API ─────────────────────────────────────────────

    async def search(self, q: str, *, category: str | None = None) -> list[PluginMeta]:
        return await self.hub.search(q, category=category)

    async def inspect(self, name: str) -> PluginMeta | None:
        return await self.hub.inspect(name)

    async def audit(self, name: str, *, version: str | None = None) -> QualityReport:
        meta = await self.hub.inspect(name)
        if meta is None:
            raise PluginError(f"plugin not found in hub: {name}")
        stage = await self.installer.download_and_extract(meta)
        try:
            return await self.auditor.audit(stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    async def install(
        self,
        name: str,
        *,
        version: str | None = None,
        permissions: list[str] | None = None,
        force: bool = False,
    ) -> InstalledPlugin:
        meta = await self.hub.inspect(name)
        if meta is None:
            raise PluginError(f"plugin not found in hub: {name}")
        stage = await self.installer.download_and_extract(meta)
        try:
            report = await self.auditor.audit(stage)
            if report.blocked and not force:
                raise PluginError(
                    f"audit blocked install (score={report.score}, "
                    f"blockers={[b.rule for b in report.blockers]})"
                )
            final = self.installer.promote(stage, name=name, version=meta.version)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        self.registry.upsert(
            name=name, version=meta.version, status="installed",
            score=report.score, permissions=permissions or [],
            install_path=str(final),
        )
        return self.registry.get(name)  # type: ignore[return-value]

    async def uninstall(self, name: str) -> None:
        row = self.registry.get(name)
        self.registry.delete(name)
        await self.unload(name)
        if row and row.install_path:
            shutil.rmtree(row.install_path, ignore_errors=True)

    async def load_runtime(self, name: str) -> list:
        row = self.registry.get(name)
        if row is None:
            raise PluginError(f"plugin not installed: {name}")
        if name in self._sandboxes:
            await self.unload(name)
        sb = RuntimeSandbox(plugin_dir=row.install_path, permissions=row.permissions)
        try:
            await sb.start()
        except Exception as e:
            self.registry.upsert(
                name=name, version=row.version, status="error", score=row.score,
                permissions=row.permissions, install_path=row.install_path,
                last_error=str(e),
            )
            raise
        self._sandboxes[name] = sb
        return [self._make_proxy_tool(sb, tname) for tname in sb.tool_names()]

    async def unload(self, name: str) -> None:
        sb = self._sandboxes.pop(name, None)
        if sb is not None:
            await sb.stop()

    async def reload(self, name: str) -> list:
        await self.unload(name)
        return await self.load_runtime(name)

    def list_installed(self) -> list[InstalledPlugin]:
        return self.registry.list_all()

    # ─── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_proxy_tool(sb: RuntimeSandbox, tname: str):
        """Create a LangChain StructuredTool that forwards to the sandbox."""
        async def _ainvoke(**kwargs):
            return await sb.invoke(tname, kwargs)

        def _sync_invoke(**kwargs):
            return asyncio.get_event_loop().run_until_complete(sb.invoke(tname, kwargs))

        return StructuredTool.from_function(
            coroutine=_ainvoke,
            func=_sync_invoke,
            name=tname,
            description=f"Proxied plugin tool: {tname}",
        )
```

- [ ] **Step 4: Run integration tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_manager_integration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd D:/agentic
git add agent/plugins/manager.py tests/plugins/test_manager_integration.py
git commit -m "feat(plugins): PluginManager facade + end-to-end integration"
```

---

## Task 9: Server routes + Socket.IO events

Wire `PluginManager` behind 7 HTTP routes on the existing aiohttp server. Emit Socket.IO events the Ink CLI subscribes to.

**Files:**
- Modify: `server/main.py` (add globals, routes, cleanup hook)

- [ ] **Step 1: Add imports and globals**

Edit `server/main.py`. After the existing `from agent.team.quality_intel import QualityIntelTeam` line, add:

```python
from agent.plugins.manager import PluginManager, PluginError
import config as _plugins_config
```

After the `quality_task: "asyncio.Task | None" = None` line, add:

```python
# PluginManager global state
plugin_manager: "PluginManager | None" = None
```

- [ ] **Step 2: Add routes**

Still in `server/main.py`, after the `# ── QualityIntel Routes ────` block and before `# ── App Factory ────`, add:

```python
# ── Plugin Routes ──────────────────────────────────────────

def _ensure_plugin_manager() -> PluginManager:
    if plugin_manager is None:
        raise web.HTTPServiceUnavailable(text="plugin manager not ready")
    return plugin_manager


@routes.get('/api/plugins')
async def plugins_list(request: web.Request):
    mgr = _ensure_plugin_manager()
    rows = mgr.list_installed()
    return web.json_response([
        {
            "name": r.name, "version": r.version, "status": r.status,
            "score": r.score, "permissions": r.permissions,
            "installed_at": r.installed_at, "last_error": r.last_error,
        }
        for r in rows
    ])


@routes.get('/api/plugins/search')
async def plugins_search(request: web.Request):
    mgr = _ensure_plugin_manager()
    q = request.query.get("q", "")
    category = request.query.get("category")
    results = await mgr.search(q, category=category)
    return web.json_response([r.__dict__ for r in results])


@routes.post('/api/plugins/inspect')
async def plugins_inspect(request: web.Request):
    mgr = _ensure_plugin_manager()
    body = await request.json()
    meta = await mgr.inspect(body["name"])
    if meta is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(meta.__dict__)


@routes.post('/api/plugins/audit')
async def plugins_audit(request: web.Request):
    mgr = _ensure_plugin_manager()
    body = await request.json()
    name = body["name"]
    await sio.emit("plugins:audit_start", {"name": name})
    try:
        report = await mgr.audit(name)
    except Exception as e:
        await sio.emit("plugins:error", {"name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)
    payload = {
        "name": name,
        "score": report.score,
        "blocked": report.blocked,
        "issues": [i.__dict__ for i in report.issues],
        "blockers": [b.__dict__ for b in report.blockers],
    }
    await sio.emit("plugins:audit_done", payload)
    return web.json_response(payload)


@routes.post('/api/plugins/install')
async def plugins_install(request: web.Request):
    mgr = _ensure_plugin_manager()
    body = await request.json()
    name = body["name"]
    try:
        installed = await mgr.install(
            name,
            version=body.get("version"),
            permissions=body.get("permissions") or [],
            force=bool(body.get("force")),
        )
        await mgr.load_runtime(name)
        await sio.emit("plugins:installed", {"name": name, "version": installed.version})
        return web.json_response({
            "status": "installed", "name": name, "version": installed.version,
        })
    except Exception as e:
        await sio.emit("plugins:error", {"name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)


@routes.post('/api/plugins/uninstall')
async def plugins_uninstall(request: web.Request):
    mgr = _ensure_plugin_manager()
    body = await request.json()
    name = body["name"]
    await mgr.uninstall(name)
    await sio.emit("plugins:uninstalled", {"name": name})
    return web.json_response({"status": "uninstalled"})


@routes.post('/api/plugins/reload')
async def plugins_reload(request: web.Request):
    mgr = _ensure_plugin_manager()
    body = await request.json()
    name = body["name"]
    try:
        await mgr.reload(name)
        await sio.emit("plugins:installed", {"name": name, "reloaded": True})
        return web.json_response({"status": "reloaded"})
    except Exception as e:
        await sio.emit("plugins:error", {"name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)
```

- [ ] **Step 3: Initialize PluginManager at startup**

In `create_app()`, just before the line `# Auto-start the PromptIntelTeam`, insert:

```python
    # Initialize PluginManager
    global plugin_manager
    plugins_root = _plugins_config.DATA_DIR / "plugins"
    plugins_tmp = _plugins_config.DATA_DIR / "plugins_tmp"
    plugins_cache = _plugins_config.DATA_DIR / "plugins_cache"
    hub_url = getattr(_plugins_config, "HUB_INDEX_URL",
                      "https://raw.githubusercontent.com/shadowdev/shadowdev/main/hub/index.json")
    plugin_manager = PluginManager(
        hub_index_url=hub_url,
        install_root=plugins_root,
        temp_root=plugins_tmp,
        db_path=_plugins_config.DATA_DIR / "plugins.db",
        cache_dir=plugins_cache,
    )
    # Load every already-installed plugin into its sandbox
    for row in plugin_manager.list_installed():
        if row.status == "installed":
            try:
                await plugin_manager.load_runtime(row.name)
            except Exception as e:
                print(f"⚠ plugin {row.name} failed to load: {e}")
    print(f"🧩 PluginManager ready — {len(plugin_manager.list_installed())} plugin(s)")
```

- [ ] **Step 4: Add shutdown in cleanup()**

In `create_app()`'s `async def cleanup(app)` — after the quality_team block, add:

```python
        # Shut down plugin sandboxes
        if plugin_manager is not None:
            for row in plugin_manager.list_installed():
                try:
                    await plugin_manager.unload(row.name)
                except Exception:
                    pass
```

- [ ] **Step 5: Smoke-test the server boots**

Run: `cd D:/agentic && python -c "import asyncio; from server.main import create_app; app = asyncio.get_event_loop().run_until_complete(create_app()); print('routes:', len([r for r in app.router.routes() if '/api/plugins' in str(r.resource)]))"`
Expected: prints `routes: 7` (the seven plugin routes).

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add server/main.py
git commit -m "feat(plugins): 7 aiohttp routes + Socket.IO events + auto-load on boot"
```

---

## Task 10: Ink `usePlugins` hook

React hook subscribing to `plugins:*` socket events + exposing fetch helpers.

**Files:**
- Create: `ink-cli/src/hooks/usePlugins.ts`

- [ ] **Step 1: Write the hook**

Create `ink-cli/src/hooks/usePlugins.ts`:

```typescript
import { useState, useEffect, useCallback } from 'react';
import type { Socket } from 'socket.io-client';

export interface InstalledPlugin {
  name: string;
  version: string;
  status: 'installed' | 'disabled' | 'error';
  score: number;
  permissions: string[];
  installed_at: string;
  last_error: string | null;
}

export interface HubPlugin {
  name: string;
  version: string;
  author: string;
  description: string;
  category: string;
  tags: string[];
  permissions: string[];
  tool_count: number;
  size_bytes: number;
  url: string;
  sha256: string;
}

export interface AuditIssue {
  rule: string;
  message: string;
  severity: 'high' | 'medium' | 'low';
  file: string;
  line: number;
}

export interface AuditResult {
  name: string;
  score: number;
  blocked: boolean;
  issues: AuditIssue[];
  blockers: AuditIssue[];
}

const BACKEND = 'http://localhost:8000';

export function usePlugins(socket: Socket | null) {
  const [installed, setInstalled] = useState<InstalledPlugin[]>([]);
  const [hubResults, setHubResults] = useState<HubPlugin[]>([]);
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const refreshInstalled = useCallback(async () => {
    const r = await fetch(`${BACKEND}/api/plugins`);
    setInstalled(await r.json());
  }, []);

  const searchHub = useCallback(async (q: string, category?: string) => {
    const params = new URLSearchParams({ q });
    if (category) params.set('category', category);
    const r = await fetch(`${BACKEND}/api/plugins/search?${params}`);
    setHubResults(await r.json());
  }, []);

  const runAudit = useCallback(async (name: string): Promise<AuditResult> => {
    const r = await fetch(`${BACKEND}/api/plugins/audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const result = await r.json();
    setAudit(result);
    return result;
  }, []);

  const install = useCallback(async (
    name: string,
    permissions: string[],
    force = false,
  ): Promise<{ status: string } | { error: string }> => {
    const r = await fetch(`${BACKEND}/api/plugins/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, permissions, force }),
    });
    return r.json();
  }, []);

  const uninstall = useCallback(async (name: string) => {
    await fetch(`${BACKEND}/api/plugins/uninstall`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    await refreshInstalled();
  }, [refreshInstalled]);

  useEffect(() => {
    refreshInstalled().catch(() => {});
  }, [refreshInstalled]);

  useEffect(() => {
    if (!socket) return;
    const onInstalled = () => refreshInstalled();
    const onUninstalled = () => refreshInstalled();
    const onError = (data: { name: string; error: string }) => setLastError(`${data.name}: ${data.error}`);
    socket.on('plugins:installed', onInstalled);
    socket.on('plugins:uninstalled', onUninstalled);
    socket.on('plugins:error', onError);
    return () => {
      socket.off('plugins:installed', onInstalled);
      socket.off('plugins:uninstalled', onUninstalled);
      socket.off('plugins:error', onError);
    };
  }, [socket, refreshInstalled]);

  return {
    installed,
    hubResults,
    audit,
    lastError,
    refreshInstalled,
    searchHub,
    runAudit,
    install,
    uninstall,
  };
}
```

- [ ] **Step 2: Commit**

```bash
cd D:/agentic
git add ink-cli/src/hooks/usePlugins.ts
git commit -m "feat(plugins): usePlugins hook — installed + search + audit + install"
```

---

## Task 11: PluginPicker component (browse UI)

Split-panel: installed plugins left, hub search results right. Simple keyboard navigation.

**Files:**
- Create: `ink-cli/src/components/PluginPicker.tsx`

- [ ] **Step 1: Write the component**

Create `ink-cli/src/components/PluginPicker.tsx`:

```typescript
import React, { useEffect, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
import { theme } from '../theme.js';
import type { InstalledPlugin, HubPlugin } from '../hooks/usePlugins.js';

interface Props {
  installed: InstalledPlugin[];
  hubResults: HubPlugin[];
  onSearch: (q: string) => void;
  onInstall: (name: string) => void;
  onUninstall: (name: string) => void;
  onClose: () => void;
}

export default function PluginPicker({
  installed, hubResults, onSearch, onInstall, onUninstall, onClose,
}: Props) {
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState<'search' | 'installed' | 'hub'>('search');
  const [selInstalled, setSelInstalled] = useState(0);
  const [selHub, setSelHub] = useState(0);

  useEffect(() => { onSearch(query); }, [query, onSearch]);

  useInput((input, key) => {
    if (key.escape) { onClose(); return; }
    if (key.tab) {
      setFocus(f => f === 'search' ? 'installed' : f === 'installed' ? 'hub' : 'search');
      return;
    }
    if (focus === 'installed') {
      if (key.upArrow)   setSelInstalled(i => Math.max(0, i - 1));
      if (key.downArrow) setSelInstalled(i => Math.min(installed.length - 1, i + 1));
      if (key.delete || input === 'd') {
        const p = installed[selInstalled];
        if (p) onUninstall(p.name);
      }
    }
    if (focus === 'hub') {
      if (key.upArrow)   setSelHub(i => Math.max(0, i - 1));
      if (key.downArrow) setSelHub(i => Math.min(hubResults.length - 1, i + 1));
      if (key.return) {
        const p = hubResults[selHub];
        if (p) onInstall(p.name);
      }
    }
  });

  const scoreColor = (s: number) => s < 40 ? theme.red : s < 70 ? theme.yellow : theme.green;

  return (
    <Box
      borderStyle="round"
      borderColor={theme.accent}
      flexDirection="column"
      paddingX={1}
      marginX={1}
    >
      <Box gap={2}>
        <Text color={theme.accent} bold>◆ Plugins</Text>
        <Text color={theme.textDim}>Tab: switch pane · Enter: install · d: uninstall · Esc: close</Text>
      </Box>

      <Box marginTop={1}>
        <Text color={focus === 'search' ? theme.accentBright : theme.textDim}>Search: </Text>
        <TextInput value={query} onChange={setQuery} focus={focus === 'search'} />
      </Box>

      <Box marginTop={1} flexDirection="row">
        {/* Installed */}
        <Box flexDirection="column" width="50%" paddingRight={1}>
          <Text color={focus === 'installed' ? theme.accentBright : theme.textDim} bold>
            INSTALLED ({installed.length})
          </Text>
          {installed.length === 0 && <Text color={theme.textDim}>(none)</Text>}
          {installed.map((p, i) => (
            <Box key={p.name} gap={1}>
              <Text color={i === selInstalled && focus === 'installed' ? theme.accent : theme.text}>
                {i === selInstalled && focus === 'installed' ? '▸ ' : '  '}{p.name}
              </Text>
              <Text color={theme.textDim}>v{p.version}</Text>
              <Text color={scoreColor(p.score)}>{p.score}/100</Text>
              {p.status === 'error' && <Text color={theme.red}>error</Text>}
            </Box>
          ))}
        </Box>

        {/* Hub */}
        <Box flexDirection="column" width="50%" paddingLeft={1}>
          <Text color={focus === 'hub' ? theme.accentBright : theme.textDim} bold>
            HUB ({hubResults.length})
          </Text>
          {hubResults.length === 0 && <Text color={theme.textDim}>(type to search)</Text>}
          {hubResults.map((p, i) => (
            <Box key={p.name} gap={1}>
              <Text color={i === selHub && focus === 'hub' ? theme.accent : theme.text}>
                {i === selHub && focus === 'hub' ? '▸ ' : '  '}{p.name}
              </Text>
              <Text color={theme.textDim}>v{p.version}</Text>
              <Text color={theme.textMuted}>{p.tool_count} tools</Text>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd D:/agentic
git add ink-cli/src/components/PluginPicker.tsx
git commit -m "feat(plugins): PluginPicker split-panel UI"
```

---

## Task 12: InstallWizard component (4-step modal)

Drives inspect → permissions → audit → confirm flow.

**Files:**
- Create: `ink-cli/src/components/InstallWizard.tsx`
- Create: `ink-cli/src/components/QualityReport.tsx`

- [ ] **Step 1: Write QualityReport first**

Create `ink-cli/src/components/QualityReport.tsx`:

```typescript
import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { AuditResult } from '../hooks/usePlugins.js';

interface Props { report: AuditResult; }

const sevColor = (s: string) => s === 'high' ? theme.red : s === 'medium' ? theme.yellow : theme.textDim;

export default function QualityReport({ report }: Props) {
  const scoreColor = report.score < 40 ? theme.red : report.score < 70 ? theme.yellow : theme.green;
  return (
    <Box flexDirection="column">
      <Box gap={2}>
        <Text color={theme.textBright}>Quality score:</Text>
        <Text color={scoreColor} bold>{report.score}/100</Text>
        {report.blocked && <Text color={theme.red} bold>BLOCKED</Text>}
      </Box>
      {report.blockers.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.red} bold>Blockers:</Text>
          {report.blockers.slice(0, 8).map((b, i) => (
            <Text key={i} color={sevColor(b.severity)}>
              · [{b.rule}] {b.message} {b.file && `(${b.file}:${b.line})`}
            </Text>
          ))}
        </Box>
      )}
      {report.issues.length > 0 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.yellow} bold>Warnings ({report.issues.length}):</Text>
          {report.issues.slice(0, 5).map((b, i) => (
            <Text key={i} color={sevColor(b.severity)}>
              · [{b.rule}] {b.message}
            </Text>
          ))}
        </Box>
      )}
    </Box>
  );
}
```

- [ ] **Step 2: Write InstallWizard**

Create `ink-cli/src/components/InstallWizard.tsx`:

```typescript
import React, { useState, useEffect } from 'react';
import { Box, Text, useInput } from 'ink';
import Spinner from 'ink-spinner';
import { theme } from '../theme.js';
import type { HubPlugin, AuditResult } from '../hooks/usePlugins.js';
import QualityReport from './QualityReport.js';

type Step = 1 | 2 | 3 | 4;

interface Props {
  plugin: HubPlugin;
  onAudit: (name: string) => Promise<AuditResult>;
  onInstall: (name: string, perms: string[], force?: boolean) => Promise<{ status: string } | { error: string }>;
  onClose: () => void;
}

export default function InstallWizard({ plugin, onAudit, onInstall, onClose }: Props) {
  const [step, setStep] = useState<Step>(1);
  const [grantedPerms, setGrantedPerms] = useState<Record<string, boolean>>(
    Object.fromEntries(plugin.permissions.map(p => [p, true]))
  );
  const [permIdx, setPermIdx] = useState(0);
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [auditing, setAuditing] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Fire audit when entering step 3
  useEffect(() => {
    if (step === 3 && !audit && !auditing) {
      setAuditing(true);
      onAudit(plugin.name)
        .then(r => { setAudit(r); setAuditing(false); })
        .catch(e => { setMessage(String(e)); setAuditing(false); });
    }
  }, [step, audit, auditing, plugin.name, onAudit]);

  useInput((input, key) => {
    if (key.escape) { onClose(); return; }
    if (key.return) {
      if (step === 1) setStep(2);
      else if (step === 2) setStep(3);
      else if (step === 3 && audit && !audit.blocked) setStep(4);
      else if (step === 4 && !installing) {
        setInstalling(true);
        const perms = Object.entries(grantedPerms).filter(([, v]) => v).map(([k]) => k);
        onInstall(plugin.name, perms).then(r => {
          if ('error' in r) { setMessage(r.error); setInstalling(false); }
          else { setMessage('installed!'); setTimeout(onClose, 1000); }
        });
      }
    }
    if (input === 'b' && step > 1) setStep((step - 1) as Step);
    if (step === 2) {
      if (key.upArrow)   setPermIdx(i => Math.max(0, i - 1));
      if (key.downArrow) setPermIdx(i => Math.min(plugin.permissions.length - 1, i + 1));
      if (input === ' ') {
        const p = plugin.permissions[permIdx];
        if (p) setGrantedPerms(g => ({ ...g, [p]: !g[p] }));
      }
    }
  });

  return (
    <Box
      borderStyle="double"
      borderColor={theme.accent}
      flexDirection="column"
      paddingX={2}
      paddingY={1}
      marginX={1}
    >
      <Box gap={2}>
        <Text color={theme.accent} bold>◆ Install {plugin.name}</Text>
        <Text color={theme.textDim}>Step {step}/4 · Enter: next · b: back · Esc: cancel</Text>
      </Box>

      {step === 1 && (
        <Box flexDirection="column" marginTop={1}>
          <Text><Text color={theme.textBright}>Version:</Text> {plugin.version}</Text>
          <Text><Text color={theme.textBright}>Author:</Text> {plugin.author || '—'}</Text>
          <Text><Text color={theme.textBright}>Category:</Text> {plugin.category || '—'}</Text>
          <Text><Text color={theme.textBright}>Tools:</Text> {plugin.tool_count}</Text>
          <Text><Text color={theme.textBright}>Size:</Text> {plugin.size_bytes} bytes</Text>
          <Text color={theme.textMuted} wrap="wrap">{plugin.description}</Text>
        </Box>
      )}

      {step === 2 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.textDim}>Toggle with Space. Unchecked perms are denied at runtime.</Text>
          {plugin.permissions.length === 0 && <Text color={theme.textDim}>(no permissions requested)</Text>}
          {plugin.permissions.map((p, i) => (
            <Text key={p} color={i === permIdx ? theme.accent : theme.text}>
              {i === permIdx ? '▸ ' : '  '}
              [{grantedPerms[p] ? 'x' : ' '}] {p}
            </Text>
          ))}
        </Box>
      )}

      {step === 3 && (
        <Box flexDirection="column" marginTop={1}>
          {auditing && <Box gap={1}><Text color={theme.accent}><Spinner type="dots" /></Text><Text>Running audit…</Text></Box>}
          {audit && <QualityReport report={audit} />}
          {audit?.blocked && <Text color={theme.red} bold marginTop={1}>Install blocked. Only Esc available.</Text>}
        </Box>
      )}

      {step === 4 && (
        <Box flexDirection="column" marginTop={1}>
          <Text color={theme.green}>Ready to install.</Text>
          <Text color={theme.textDim}>Granted permissions: {Object.entries(grantedPerms).filter(([, v]) => v).map(([k]) => k).join(', ') || '(none)'}</Text>
          <Text color={theme.textDim}>Press Enter to confirm.</Text>
          {installing && <Box gap={1}><Text color={theme.accent}><Spinner type="dots" /></Text><Text>Installing…</Text></Box>}
          {message && <Text color={message === 'installed!' ? theme.green : theme.red}>{message}</Text>}
        </Box>
      )}
    </Box>
  );
}
```

- [ ] **Step 3: Smoke-build ink-cli**

Run: `cd D:/agentic/ink-cli && npx tsc --noEmit 2>&1 | tail -10`
Expected: no errors (may have warnings from other files, but no new errors from these three files).

- [ ] **Step 4: Commit**

```bash
cd D:/agentic
git add ink-cli/src/components/InstallWizard.tsx ink-cli/src/components/QualityReport.tsx
git commit -m "feat(plugins): InstallWizard 4-step modal + QualityReport"
```

---

## Task 13: Wire into App.tsx and add slash commands

**Files:**
- Modify: `ink-cli/src/App.tsx`
- Modify: `ink-cli/src/components/InputBox.tsx`

- [ ] **Step 1: Add slash commands**

Edit `ink-cli/src/components/InputBox.tsx`. In `ALL_COMMANDS`, after the `/quality` entry, add four rows:

```typescript
  { cmd: '/plugins',           desc: 'Browse installed & hub plugins',                  instant: true },
  { cmd: '/plugin install',    desc: 'Install a plugin from the hub (/plugin install <name>)' },
  { cmd: '/plugin audit',      desc: 'Run quality audit (/plugin audit <name>)' },
  { cmd: '/plugin uninstall',  desc: 'Uninstall a plugin (/plugin uninstall <name>)' },
```

- [ ] **Step 2: Wire hook and wizard into App.tsx**

Edit `ink-cli/src/App.tsx`.

Add import near the top (after `useQuality`):

```typescript
import { usePlugins, type HubPlugin } from './hooks/usePlugins.js';
import PluginPicker from './components/PluginPicker.js';
import InstallWizard from './components/InstallWizard.js';
```

Inside the `App()` function body, after `const quality = useQuality(socket.socket);`, add:

```typescript
  const plugins = usePlugins(socket.socket);
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [wizardPlugin, setWizardPlugin] = useState<HubPlugin | null>(null);
```

In the `handleSubmit` callback (the slash-command switch), add these cases just after the existing `/quality stop` block:

```typescript
    if (trimmed === '/plugins') {
      setPluginsOpen(true);
      return;
    }
    if (trimmed.startsWith('/plugin install ')) {
      const name = trimmed.slice('/plugin install '.length).trim();
      fetch(`${BACKEND}/api/plugins/inspect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
        .then(r => r.json())
        .then((meta: HubPlugin | { error: string }) => {
          if ('error' in meta) socket.injectMessage(`Plugin not found: ${name}`);
          else setWizardPlugin(meta);
        })
        .catch(err => socket.injectMessage(`Failed to inspect: ${String(err)}`));
      return;
    }
    if (trimmed.startsWith('/plugin audit ')) {
      const name = trimmed.slice('/plugin audit '.length).trim();
      socket.injectMessage(`Auditing ${name}…`);
      plugins.runAudit(name)
        .then(r => socket.injectMessage(
          `Audit: ${r.score}/100${r.blocked ? ' BLOCKED' : ''} — blockers:${r.blockers.length} warnings:${r.issues.length}`
        ))
        .catch(e => socket.injectMessage(`Audit failed: ${String(e)}`));
      return;
    }
    if (trimmed.startsWith('/plugin uninstall ')) {
      const name = trimmed.slice('/plugin uninstall '.length).trim();
      plugins.uninstall(name)
        .then(() => socket.injectMessage(`Uninstalled ${name}`))
        .catch(e => socket.injectMessage(`Uninstall failed: ${String(e)}`));
      return;
    }
```

In the `disabled` prop of `InputBox`, add `|| pluginsOpen || wizardPlugin !== null`:

```typescript
      <InputBox
        onSubmit={handleSubmit}
        onCancel={handleCancel}
        disabled={pickerOpen || modelPickerOpen || pluginsOpen || wizardPlugin !== null}
        streaming={socket.streaming}
      />
```

In the `useInput` hook at the top of App — where it returns early for `pickerOpen || modelPickerOpen` — extend the condition:

```typescript
    if (pickerOpen || modelPickerOpen || pluginsOpen || wizardPlugin) return;
```

In the JSX render, add overlays just after the existing `{modelPickerOpen && ...}` block:

```tsx
      {pluginsOpen && (
        <PluginPicker
          installed={plugins.installed}
          hubResults={plugins.hubResults}
          onSearch={(q) => plugins.searchHub(q).catch(() => {})}
          onInstall={(name) => {
            fetch(`${BACKEND}/api/plugins/inspect`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name }),
            }).then(r => r.json()).then((meta: HubPlugin) => {
              setPluginsOpen(false);
              setWizardPlugin(meta);
            });
          }}
          onUninstall={(name) => plugins.uninstall(name)}
          onClose={() => setPluginsOpen(false)}
        />
      )}
      {wizardPlugin && (
        <InstallWizard
          plugin={wizardPlugin}
          onAudit={plugins.runAudit}
          onInstall={plugins.install}
          onClose={() => { setWizardPlugin(null); plugins.refreshInstalled(); }}
        />
      )}
```

- [ ] **Step 3: Smoke-build ink-cli**

Run: `cd D:/agentic/ink-cli && npx tsc --noEmit 2>&1 | tail -10`
Expected: no new TypeScript errors.

- [ ] **Step 4: Commit**

```bash
cd D:/agentic
git add ink-cli/src/App.tsx ink-cli/src/components/InputBox.tsx
git commit -m "feat(plugins): wire /plugins + /plugin install|audit|uninstall commands"
```

---

## Task 14: Graph integration — load sandboxed plugin tools alongside legacy

Sandboxed plugins need to appear in the agent's tool list. Add a small adapter in `graph.py` that asks `plugin_manager` for live proxy tools at graph-build time.

**Files:**
- Modify: `agent/graph.py`

- [ ] **Step 1: Find the injection point**

Run: `cd D:/agentic && grep -n "get_plugin_tools\|ALL_TOOLS" agent/graph.py | head -10`
Expected: shows where `get_plugin_tools(...)` is called (from `plugin_registry`). We'll inject sandboxed tools at the same spot.

- [ ] **Step 2: Add the adapter**

Edit `agent/graph.py`. At the top of the file, near other plugin imports, add:

```python
from agent.plugins.manager import PluginManager
```

In the `build_graph` function, immediately after the existing `planner_tools, coder_only = get_plugin_tools(existing_names=...)` call, add:

```python
    # Sandboxed plugins — load from the manager singleton if one exists.
    # (The server sets `_PLUGIN_MANAGER_SINGLETON` in main.py at startup.)
    sandbox_tools: list = []
    from agent.plugins import manager as _pm_mod
    _pm = getattr(_pm_mod, "_SINGLETON", None)
    if _pm is not None:
        for row in _pm.list_installed():
            if row.status != "installed":
                continue
            try:
                sandbox_tools.extend(
                    asyncio.get_event_loop().run_until_complete(_pm.load_runtime(row.name))
                )
            except Exception as exc:
                print(f"⚠ sandbox load failed for {row.name}: {exc}")
    planner_tools.extend(sandbox_tools)
    coder_only.extend(sandbox_tools)
```

- [ ] **Step 3: Register the singleton in manager.py**

Edit `agent/plugins/manager.py` — at the very end of the file, add:

```python
# ─── Module-level singleton registry ─────────────────────────
# Set by server/main.py at startup so agent/graph.py can find the live instance
# without importing server code (which would cause a circular import).
_SINGLETON: PluginManager | None = None


def set_singleton(mgr: PluginManager) -> None:
    global _SINGLETON
    _SINGLETON = mgr


def get_singleton() -> PluginManager | None:
    return _SINGLETON
```

- [ ] **Step 4: Wire singleton in server/main.py**

Edit `server/main.py` — in the `create_app()` initialization block you added in Task 9, right after constructing `plugin_manager`, add:

```python
    from agent.plugins.manager import set_singleton
    set_singleton(plugin_manager)
```

- [ ] **Step 5: Verify imports still resolve**

Run: `cd D:/agentic && python -c "from agent.graph import build_graph; print('ok')" 2>&1 | tail -3`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add agent/graph.py agent/plugins/manager.py server/main.py
git commit -m "feat(plugins): integrate sandboxed plugin tools into agent graph"
```

---

## Task 15: Startup sweep — remove orphan install dirs

If a plugin's install dir exists but no DB row does (or vice versa), clean up at boot.

**Files:**
- Modify: `agent/plugins/manager.py`

- [ ] **Step 1: Write failing test**

Append to `tests/plugins/test_manager_integration.py`:

```python
@pytest.mark.asyncio
async def test_startup_sweep_removes_orphan_dir(tmp_path, fake_hub):
    # Orphan install dir, no DB row
    orphan = tmp_path / "plugins" / "ghost-1.0.0"
    orphan.mkdir(parents=True)
    (orphan / "plugin.json").write_text("{}")

    mgr = PluginManager(
        hub_index_url=fake_hub["url"],
        install_root=tmp_path / "plugins",
        temp_root=tmp_path / "tmp",
        db_path=tmp_path / "plugins.db",
        cache_dir=tmp_path / "cache",
    )
    mgr.startup_sweep()
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_startup_sweep_marks_missing_dir_error(tmp_path, fake_hub):
    # DB row exists but install dir is gone
    mgr = PluginManager(
        hub_index_url=fake_hub["url"],
        install_root=tmp_path / "plugins",
        temp_root=tmp_path / "tmp",
        db_path=tmp_path / "plugins.db",
        cache_dir=tmp_path / "cache",
    )
    mgr.registry.upsert(
        name="lost", version="1", status="installed", score=80,
        permissions=[], install_path=str(tmp_path / "plugins" / "lost-1"),
    )
    mgr.startup_sweep()
    row = mgr.registry.get("lost")
    assert row is not None
    assert row.status == "error"
    assert "not found" in (row.last_error or "").lower()
```

- [ ] **Step 2: Run tests — expect fail**

Run: `cd D:/agentic && pytest tests/plugins/test_manager_integration.py::test_startup_sweep_removes_orphan_dir -v`
Expected: FAIL — `AttributeError: 'PluginManager' object has no attribute 'startup_sweep'`.

- [ ] **Step 3: Implement startup_sweep**

Edit `agent/plugins/manager.py`. Add this method to `PluginManager` (before the closing of the class):

```python
    def startup_sweep(self) -> None:
        """Reconcile filesystem and DB state at boot.

        - install_root dirs not in the DB → removed.
        - DB rows whose install_path is missing → marked status='error'.
        """
        install_root = self.installer.install_root
        db_rows = {r.name: r for r in self.registry.list_all()}
        expected_dirs = {Path(r.install_path).name for r in db_rows.values()}

        # 1. Remove orphan dirs
        if install_root.is_dir():
            for child in install_root.iterdir():
                if child.is_dir() and child.name not in expected_dirs:
                    shutil.rmtree(child, ignore_errors=True)
                    logger.info("plugin sweep: removed orphan dir %s", child)

        # 2. Mark missing-dir rows as error
        for row in db_rows.values():
            if not Path(row.install_path).is_dir():
                self.registry.upsert(
                    name=row.name, version=row.version, status="error",
                    score=row.score, permissions=row.permissions,
                    install_path=row.install_path,
                    last_error="install directory not found",
                )
```

- [ ] **Step 4: Call startup_sweep in server/main.py**

In `create_app()`, right after `plugin_manager = PluginManager(...)`, insert:

```python
    plugin_manager.startup_sweep()
```

- [ ] **Step 5: Run tests — expect pass**

Run: `cd D:/agentic && pytest tests/plugins/test_manager_integration.py -v`
Expected: 5 passed (3 from Task 8 + 2 new).

- [ ] **Step 6: Commit**

```bash
cd D:/agentic
git add agent/plugins/manager.py server/main.py tests/plugins/test_manager_integration.py
git commit -m "feat(plugins): startup sweep — reconcile FS and DB state"
```

---

## Task 16: Full-stack smoke test (manual)

One-shot script the developer runs once to prove the whole stack works.

**Files:**
- Create: `tests/plugins/bench_e2e.sh`

- [ ] **Step 1: Write the bench script**

Create `tests/plugins/bench_e2e.sh`:

```bash
#!/usr/bin/env bash
# End-to-end smoke test for the plugin system.
# Requires: server running on :8000, hub reachable OR override HUB_INDEX_URL.

set -euo pipefail
BACKEND="${BACKEND:-http://localhost:8000}"

echo "▸ health"
curl -sf "$BACKEND/health" >/dev/null && echo "  ok"

echo "▸ search 'demo'"
curl -sf "$BACKEND/api/plugins/search?q=demo" | head -c 500
echo

echo "▸ list installed"
curl -sf "$BACKEND/api/plugins" | head -c 500
echo

echo "▸ audit demo (expected: scored)"
curl -sf -X POST "$BACKEND/api/plugins/audit" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo"}' | head -c 500
echo

echo "All endpoints reachable."
```

- [ ] **Step 2: Commit**

```bash
cd D:/agentic
chmod +x tests/plugins/bench_e2e.sh
git add tests/plugins/bench_e2e.sh
git commit -m "chore(plugins): E2E smoke test script"
```

---

## Task 17: Full test run

Confirm everything passes together.

- [ ] **Step 1: Run the full plugin test suite**

Run: `cd D:/agentic && pytest tests/plugins/ -v --tb=short 2>&1 | tail -40`
Expected: ~30 passed (types: 4, registry: 6, hub_scout: 6, installer: 5, auditor: 4, rpc: 4, sandbox: 5, manager: 5).

If any test fails, read the failure carefully — do **not** mark "fixable" issues as skipped. Fix at root.

- [ ] **Step 2: Run the rest of the test suite (regression check)**

Run: `cd D:/agentic && pytest --tb=short 2>&1 | tail -10`
Expected: previous count (790) + ~30 new = ~820 passed.

- [ ] **Step 3: TypeScript typecheck**

Run: `cd D:/agentic/ink-cli && npx tsc --noEmit 2>&1 | tail -10`
Expected: no errors.

- [ ] **Step 4: Commit any accumulated fixes (if any)**

```bash
cd D:/agentic
git status
# if dirty:
git add -A && git commit -m "fix(plugins): resolve test failures discovered in full run"
```

---

## Self-review notes

Checked spec-to-task mapping:

- **Goals 1 (browse & install)** → Tasks 10–13.
- **Goal 2 (quality gate)** → Task 5 (auditor) + Task 8 (install blocks on `report.blocked`).
- **Goal 3 (runtime isolation)** → Tasks 6, 7.
- **Goal 4 (no regression)** → Task 14 adapter loads sandboxed tools alongside the existing `get_plugin_tools` entry-point path.
- **Error taxonomy (spec §Error handling)** → tested in Tasks 4 (IntegrityFailure, BadArchiveError), 5 (QualityBlocked), 7 (SandboxError, PermissionDenied), 8 (rollback), 15 (sweep).
- **Security boundaries** → Task 7 (network, fs, subprocess, timeout) covers the four enforced gates. `RLIMIT_AS` / `RLIMIT_CPU` are not implemented in v1 — add as a follow-up if the memory benchmark shows plugins exceeding budget. The spec lists these as enforced, so **I'm flagging this as a known v1 gap** to revisit.
- **Seven HTTP routes** → Task 9.
- **12 new files** → Tasks 1–14 cover all 12. Tick count:
  1. `agent/plugins/__init__.py` ✓ (Task 1)
  2. `agent/plugins/types.py` ✓ (Task 1)
  3. `agent/plugins/registry_db.py` ✓ (Task 2)
  4. `agent/plugins/hub_scout.py` ✓ (Task 3)
  5. `agent/plugins/installer.py` ✓ (Task 4)
  6. `agent/plugins/auditor.py` ✓ (Task 5)
  7. `agent/plugins/sandbox.py` ✓ (Task 7)
  8. `agent/plugins/manager.py` ✓ (Task 8)
  9. `shadowdev/plugin_host.py` ✓ (Task 6)
  10. `ink-cli/src/hooks/usePlugins.ts` ✓ (Task 10)
  11. `ink-cli/src/components/PluginPicker.tsx` ✓ (Task 11)
  12. `ink-cli/src/components/InstallWizard.tsx` + `QualityReport.tsx` ✓ (Task 12)

Placeholder scan: no "TBD" / "TODO" / "fill in" text in tasks. All code blocks contain complete implementations.

Type consistency: `QualityReport.blocked` is used consistently (Task 1 defines it as a property; Tasks 5, 8, 12 read it). `RuntimeSandbox.invoke` signature is `(name: str, args: dict)` in both Task 7 definition and Task 8 usage.

One known gap: POSIX `resource.setrlimit` calls are not wired — v1 ships without memory/CPU caps beyond the 30s call timeout. Document this in the spec's "Out-of-scope" as v1 vs v2, or implement as a Task 18. Leaving the decision to the user.
