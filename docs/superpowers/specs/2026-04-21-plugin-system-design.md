# Plugin System with Quality Gates — Design

**Date:** 2026-04-21
**Status:** Design — approved, ready for implementation planning
**Scope:** Cohesive plugin manager with browse / install / audit / sandbox-runtime.

## Context

ShadowDev already has three scattered plugin pieces:

- `agent/plugin_registry.py` — pip `entry_points` discovery (`shadowdev.tools` group).
- `agent/skill_loader.py` — zero-config `.py` drop-in loader for `skills/_tools/`.
- `agent/skill_hub.py` — remote hub: download markdown skills / plugins from a URL.

There is no UI for plugins, no install wizard, no quality gate, and no isolation — a third-party plugin runs in the agent process with full Python access. This spec unifies the pieces under one manager, adds a review-before-install quality gate, and isolates each plugin in its own subprocess.

## Goals

1. **Browse & install** plugins from a hub (with offline fallback).
2. **Quality gate**: every plugin is statically audited (ruff / mypy / bandit / manifest validation) before it can load; score `< 60` or any blocker halts install.
3. **Runtime isolation**: each plugin runs in its own subprocess with permission-gated network / filesystem / subprocess / resource caps. A plugin crash cannot crash the agent.
4. **Zero regression**: existing entry-point and `skills/_tools/` plugins continue to work (wrapped, not rewritten).

## Non-goals

- Docker/container-per-plugin (considered, rejected as overkill for single-user IDE).
- Rating / reviews / social signals (could be added later on top of the hub index).
- Cross-language plugins (Python only for v1).
- Live hot-reload of plugin source (install/upgrade requires explicit reload).

---

## Architecture

```
┌──────────────────────── Ink CLI ────────────────────────┐
│ /plugins                → PluginPicker (browse)         │
│ /plugin install <name>  → InstallWizard (4 steps)       │
│ /plugin audit <name>    → QualityReport                 │
│ /plugin uninstall <n>   → confirmation                  │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP + Socket.IO
┌───────────────────────────▼──────────────────────────────┐
│                    PluginManager (server)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │ HubScout │ │ Installer│ │ Auditor  │ │RuntimeSandbox│ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────────┘  │
│                         │                                │
│                         ▼                                │
│               PluginRegistryDB (SQLite)                  │
└──────┬───────────────────────────────────┬───────────────┘
       │  spawn per plugin                 │
       ▼                                   ▼
┌───────────────┐  JSON-RPC over  ┌───────────────┐
│ plugin A      │ ◄─── stdio ──►  │ plugin B      │
│ (perm-gated)  │                 │ (perm-gated)  │
└───────────────┘                 └───────────────┘
```

### Five internal components

1. **HubScout** — fetches / caches the index, full-text search, returns normalised metadata.
2. **Installer** — downloads artefact, verifies SHA256 (+ optional signature), expands to `~/.shadowdev/plugins/<name>-<version>/`. Never executes plugin code.
3. **QualityAuditor** — runs ruff/mypy/bandit over plugin source; validates `plugin.json` manifest; scores 0–100; hard-blocks on score `< 60` or any blocker.
4. **RuntimeSandbox** — spawns `python -m shadowdev.plugin_host <plugin_dir>` subprocess per plugin; relays LangChain tool calls over JSON-RPC; enforces permission allowlist (net / fs / subprocess); kills on timeout.
5. **PluginRegistryDB** — SQLite table persisting install state.

Everything else wraps existing code (`plugin_registry.py`, `skill_hub.py`, `skill_loader.py`). No existing file is rewritten.

---

## Components & responsibilities

### `agent/plugins/manager.py` — `PluginManager`

Single public facade. The rest of the app imports only this.

```python
class PluginManager:
    def list_installed() -> list[InstalledPlugin]: ...
    def search(q: str, category: str | None = None) -> list[PluginMeta]: ...
    def inspect(name: str, version: str | None = None) -> PluginMeta: ...
    def audit(name: str, version: str | None = None) -> QualityReport: ...
    def install(name: str, version: str | None, perms: list[str], force: bool = False) -> InstalledPlugin: ...
    def uninstall(name: str) -> None: ...
    def load_runtime(name: str) -> list[ProxyTool]: ...
    def unload(name: str) -> None: ...
    def reload(name: str) -> list[ProxyTool]: ...
```

### `agent/plugins/hub_scout.py` — `HubScout`

Wraps the fetch logic from `skill_hub.py`. Adds 10-minute in-memory cache + 7-day on-disk cache fallback. Exposes `search(query, category=None, min_score=0)` returning `PluginMeta` dataclasses.

### `agent/plugins/installer.py` — `Installer`

Pure file ops: download, SHA256 verify, optional ed25519 signature verify, tarball extract with path-traversal guard, atomic `os.replace` promote from temp → install dir. On failure, rolls back by deleting the temp dir.

### `agent/plugins/auditor.py` — `QualityAuditor`

Reuses `QualityIntelTeam` scanners (ruff, mypy, bandit) over the plugin source directory. Adds three manifest checks:

1. `permissions` field present; each entry in vocabulary `{fs.read, fs.write, net.http, subprocess, env}`.
2. Declared `tools[]` actually exist per static AST parse (no import).
3. No top-level side effects (module-level `open()`, `requests.get()`, `subprocess.run()`, `eval()`, `exec()`).

Returns `QualityReport { score: int, issues: list, blockers: list }`. Hard-blocks install when `blockers` non-empty OR `score < 60`.

### `agent/plugins/sandbox.py` — `RuntimeSandbox`

Spawns `python -m shadowdev.plugin_host <plugin_dir>` as a subprocess. Stdin/stdout carry length-prefixed JSON-RPC 2.0 messages. Host-side proxy creates a LangChain-compatible `Tool` whose `.invoke(args)` sends `{"method": "tool.invoke", "params": {...}}` and awaits the reply.

Enforced boundaries:

- **Network**: monkey-patches `socket.socket.connect` before importing plugin code; non-allowlisted hosts raise `PermissionError`.
- **Filesystem**: monkey-patches `builtins.open`; paths must realpath into a declared root list.
- **Subprocess**: monkey-patches `subprocess.Popen` to require `subprocess` permission.
- **Timeout**: 30s per call; subprocess killed on exceed, respawned once before marking `status=error`.
- **Resource**: `RLIMIT_AS=512MB`, `RLIMIT_CPU=60s` on POSIX; Windows best-effort via Job Objects (`ctypes`), degraded to watchdog if Job creation fails.

### `shadowdev/plugin_host.py` — plugin-side RPC

New runnable module. On startup: reads handshake (permissions, allowlists), installs monkey-patches, imports the plugin, enumerates `__skill_tools__`. Serves `tool.list`, `tool.invoke`, `shutdown` methods.

### `agent/plugins/registry_db.py` — `PluginRegistryDB`

SQLite at `~/.shadowdev/plugins.db`. One table:

```sql
CREATE TABLE plugins (
  name TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  status TEXT NOT NULL,          -- installed | disabled | error
  score INTEGER NOT NULL,
  permissions TEXT NOT NULL,     -- JSON array
  install_path TEXT NOT NULL,
  installed_at TEXT NOT NULL,    -- ISO-8601 UTC
  last_audited_at TEXT NOT NULL, -- ISO-8601 UTC
  last_error TEXT                -- nullable
);
```

### `server/main.py` — seven HTTP routes

- `GET  /api/plugins` — installed plugins (DB dump).
- `GET  /api/plugins/search?q=` — hub search via `HubScout`.
- `POST /api/plugins/inspect` — `{name, version?}` → `PluginMeta`.
- `POST /api/plugins/audit` — `{name, version?}` → streams `plugins:audit_*` events, returns final `QualityReport`.
- `POST /api/plugins/install` — `{name, version, permissions, force?}` → `InstalledPlugin`.
- `POST /api/plugins/uninstall` — `{name}` → `{status: "uninstalled"}`.
- `POST /api/plugins/reload` — `{name}` → reloads sandbox.

Socket.IO events: `plugins:audit_start`, `plugins:audit_issue`, `plugins:audit_done`, `plugins:installed`, `plugins:uninstalled`, `plugins:error`.

### Ink CLI additions

- `hooks/usePlugins.ts` — subscribes to all `plugins:*` events; exposes `{installed, hubResults, audit, wizardState}`.
- `components/PluginPicker.tsx` — split-panel browse: installed on left, hub results on right. Filter by category and name.
- `components/InstallWizard.tsx` — 4-step modal: Inspect → Permissions → Audit → Confirm. Advances on Enter, goes back on Esc.
- `components/QualityReport.tsx` — report view used inside Step 3 and from `/plugin audit <name>`.
- Slash commands added to `InputBox.tsx`: `/plugins`, `/plugin install <name>`, `/plugin audit <name>`, `/plugin uninstall <name>`.

**Sizing constraint:** every new file stays under 300 lines; split if it grows past.

---

## Data flow

### Install path (happy)

```
User /plugin install deploy-fly
  ↓
Step 1 INSPECT   → POST /api/plugins/inspect → PluginMeta
Step 2 PERMISSIONS → user toggles declared perms (UI-only)
Step 3 AUDIT     → POST /api/plugins/audit → QualityReport (streamed)
                   score<60 OR blockers → hard block, only Cancel available
Step 4 INSTALL   → POST /api/plugins/install
                   Installer.promote(tmp → ~/.shadowdev/plugins/…)
                   PluginRegistryDB.upsert(status=installed, score, perms)
                   PluginManager.load_runtime() → RuntimeSandbox.spawn()
                   proxy tools registered into HookedToolNode
                   emit plugins:installed
```

### Runtime tool call (after install)

```
Agent                          Host proxy                      Plugin subprocess
  │ invoke(args)                   │                                 │
  ├──────────────────────────────► │                                 │
  │                                │ stdin: {jsonrpc,id,method,params}│
  │                                ├───────────────────────────────► │
  │                                │                           import checks
  │                                │                           perm allowlist
  │                                │                           invoke tool
  │                                │ stdout: {jsonrpc,id,result/error}│
  │                                │ ◄──────────────────────────────┤
  │                                │                                 │
  │ ◄────── result or tool-error ──┤                                 │
```

Permission violation → RPC error code `-32001 permission denied: <perm>`. Agent sees a normal tool error, never crashes.

### Search / list flow

`/plugins` → `GET /api/plugins` (installed) + `GET /api/plugins/search?q=` (hub) → Ink renders split panel.

---

## Error handling

Five failure classes, each with a fixed response pattern:

| Class | Example | Response |
|---|---|---|
| `HubUnavailable` | index fetch 5xx/timeout | Use 7-day on-disk cache; UI: "⚠ offline — cached N days ago" |
| `IntegrityFailure` | SHA256 mismatch, bad signature | Abort install, delete temp; UI shows expected vs actual hash |
| `QualityBlocked` | `score < 60` or blocker list non-empty | Abort install, show `QualityReport`. "Install anyway" requires `--force` flag AND typing plugin name to confirm |
| `SandboxError` | subprocess crash / spawn fail / timeout | Mark `status=error` in DB; agent sees tool error; one auto-retry spawn; user resolves via `/plugin reload <name>` |
| `PermissionDenied` | plugin calls `requests.get()` but `net.http` revoked | RPC `-32001`; agent sees tool error, no crash |

All five logged to `~/.shadowdev/logs/plugins-<date>.jsonl` in the existing structured format.

### Atomicity

`Installer.promote` is the only non-idempotent step. Pattern:

1. Download + audit in `<tmp>/pending-<uuid>/`.
2. On approval, `os.replace(tmp, final_path)` — atomic on same filesystem.
3. DB upsert last; if it fails, filesystem state is harmless. A startup sweep removes install paths not in the DB.

### Uninstall ordering

DB row deleted first → sandbox terminated → files removed. If file deletion fails, the row is already gone, so it's invisible; startup sweep removes orphans.

### Degraded modes

- Hub unreachable → search disabled, installed plugins still load from cache.
- One plugin's sandbox fails → its tools skipped, other plugins unaffected.
- `plugins.db` corrupt → rename to `plugins.db.bak`, start fresh, log warning.

**Agent always boots.**

---

## Security boundaries

### Enforced (hard boundary)

- **Process isolation** — plugin crash cannot crash the agent. `proc.poll()` detects death; sandbox auto-respawns once, then marks `status=error`.
- **Permission allowlist** — monkey-patches installed before plugin import: `socket.socket.connect`, `builtins.open`, `subprocess.Popen`.
- **Path traversal** — `fs.*` perms take a root list; `os.path.realpath()` prefix-checked on every `open()`. No `..`, no symlink escape.
- **Install path confinement** — plugins unpack only under `~/.shadowdev/plugins/`; archive members starting with `/` or containing `..` abort the install.
- **Timeouts** — 30s hard cap per RPC call.
- **Resource caps** — 512MB address space + 60s CPU on POSIX; Job Objects best-effort on Windows.

### Documented (soft boundary — warn, don't block)

- Broad FS perms (e.g. `fs.read=["~/"]`) give the plugin access to `.ssh/`. Auditor surfaces a warning but does not block.
- Signing is optional. Unsigned entries get a yellow "unverified" banner; signed entries a green "verified" badge.

---

## Testing strategy

Five layers, ~60 new tests.

### 1. Unit tests — pure logic (~15 tests)

`tests/plugins/test_hub_scout.py`, `test_installer.py`, `test_auditor.py`

- `HubScout`: search filtering, cache expiry, 7-day stale fallback.
- `Installer`: SHA256 pos/neg, tarball rejects `..` and absolute-path members, temp cleanup on failure.
- `Auditor`: manifest validation, top-level side-effect AST scan, score math.

No subprocess, no network. All I/O under `tmp_path`.

### 2. Contract tests — RPC protocol (~8 tests)

`tests/plugins/test_rpc_contract.py` + fixture `tests/plugins/fixtures/echo_plugin/` with three tools: `echo_ok`, `echo_slow` (60s sleep), `echo_forbidden` (attempts `requests.get`).

- JSON-RPC 2.0 message shape.
- Length-prefixed framing round-trip.
- 30s timeout cap honoured.
- Permission-denied returns `-32001` structured error.

### 3. Integration tests — install pipeline (~10 tests)

`tests/plugins/test_install_pipeline.py` — local `aiohttp.web.Application` fake-hub fixture with hand-written `index.json` and tarballs so tests never touch the network.

- Happy path end-to-end.
- Audit blocker path (fixture with `eval()` at module level).
- Score < 60 blocks; `--force` override path.
- Rollback on `os.replace` failure.
- Uninstall removes row + subprocess + files.
- Startup sweep removes orphan install dirs.

### 4. Sandbox boundary tests — security (~12 tests)

`tests/plugins/test_sandbox_boundaries.py` — one test per enforced rule, using hostile fixture plugins.

- Network: denied by default; `net.http=["api.openai.com"]` allowlist enforcement.
- FS: `fs.read=["/tmp"]` permits `/tmp/foo`, denies `/etc/passwd` AND `/tmp/../etc/passwd` after realpath.
- Subprocess without perm → `PermissionError` → RPC error.
- Crash (`os._exit(1)`) → agent sees tool error, other plugins unaffected, `status=error`.
- Timeout: 60s sleep killed at 30s; next call respawns subprocess.
- Memory: 1GB allocation → `MemoryError` on POSIX (skipped on Windows with reason string).

### 5. UI smoke tests (~5 tests)

`ink-cli/src/__tests__/PluginPicker.test.tsx`, `InstallWizard.test.tsx` using `ink-testing-library`.

- `/plugins` renders installed + hub columns.
- Wizard advances Step1→2→3→4 on Enter, back on Esc.
- Audit failure → red banner + disabled Install.
- Permission toggles persist across Step2↔3 navigation.
- Live `plugins:*` socket events update wizard in real time.

### 6. Manual bench (one-shot)

`tests/plugins/bench_e2e.sh` — install three real plugins from fake hub, invoke each tool 100× through the agent. Targets:

- RPC latency: p50 < 3ms, p99 < 10ms.
- Subprocess spawn: < 300ms.
- Idle memory per plugin: < 50MB.

### CI gate

Layers 1–4 must pass. Layer 5 runs in a separate Ink lane. Layer 6 is manual. Coverage target: 100% of new code covered by unit + integration; 80% by security tests (some POSIX-only paths skipped on Windows).

---

## Migration & backward compatibility

- Existing `entry_points`-discovered plugins continue to load via a shim: `plugin_registry.get_plugin_tools()` is still called from `graph.py`, but now it populates the new `PluginRegistryDB` as `status=installed, score=None, permissions=[]` with a one-line "legacy in-process plugin (unsandboxed)" note visible in `/plugins`. They are not migrated to sandbox automatically.
- Existing `skills/_tools/*.py` plugins behave identically (they're local zero-config skills, out of scope for the hub/sandbox story).
- New plugins from the hub always run sandboxed.
- A `--migrate-plugins` CLI flag (later enhancement, not in v1) would re-audit and move entry-point plugins into sandboxes.

---

## Out-of-scope for v1 (explicit)

- Rating / reviews / social signals.
- Cross-language plugins.
- Hot reload of edited plugin source (explicit `/plugin reload` required).
- Auto-migrate existing entry-point plugins to sandbox.
- Docker-per-plugin isolation (revisit if subprocess isolation proves insufficient).

---

## File inventory (new vs touched)

**New (12 files, each < 300 lines):**

- `agent/plugins/__init__.py`
- `agent/plugins/manager.py`
- `agent/plugins/hub_scout.py`
- `agent/plugins/installer.py`
- `agent/plugins/auditor.py`
- `agent/plugins/sandbox.py`
- `agent/plugins/registry_db.py`
- `shadowdev/plugin_host.py`
- `ink-cli/src/hooks/usePlugins.ts`
- `ink-cli/src/components/PluginPicker.tsx`
- `ink-cli/src/components/InstallWizard.tsx`
- `ink-cli/src/components/QualityReport.tsx`

**Touched:**

- `server/main.py` — six new routes, global `plugin_manager`, cleanup hook.
- `agent/graph.py` — load sandboxed plugin tools alongside existing `get_plugin_tools()` result.
- `ink-cli/src/App.tsx` — wire `usePlugins`, render `PluginPicker` / `InstallWizard`.
- `ink-cli/src/components/InputBox.tsx` — four new slash commands.

**Test files:** `tests/plugins/` directory with ~60 tests across five files plus fixtures.

---

## Summary

Ten well-bounded files + four route additions + four UI additions. Reuses `QualityIntel` scanners, the existing `skill_hub` fetch, and the existing Socket.IO event pattern. Delivers browse-and-install UI, a hard audit gate before any third-party code runs, and true subprocess-level isolation — without Docker and without rewriting any existing plugin file.
