"""PluginQualityTeam — continuous quality scanner for the plugin subsystem.

Twin of QualityIntelTeam, but scoped to:
  - agent/plugins/*.py           (the plugin manager / sandbox / installer)
  - shadowdev/plugin_host.py     (the in-subprocess host)
  - tests/plugins/**/*.py        (test coverage signal)

4 plugin-specific domains:

  sandbox_safety   — pattern-based audit of monkey-patches in plugin_host.py
                     (does the gate cover os.open / create_connection / getaddrinfo?)
  supply_chain     — installer integrity guards (sha256, signature, size caps,
                     filter="data" on extractall, symlink rejection).
  rpc_reliability  — sandbox.py: per-RPC timeout, stderr drain, IncompleteReadError
                     handling.
  test_coverage    — count of tests/plugins/*.py vs source files; required test
                     names (boundaries, signature, sweep, schema, cors).

Emits Socket.IO events for the Ink CLI / web UI:

  plugin_quality:status        { running, round, overall_score, total_issues, converged }
  plugin_quality:round_start   { round, timestamp }
  plugin_quality:scanning      { domain }
  plugin_quality:finding       { domain, issues_found, severity_breakdown }
  plugin_quality:issue         { domain, file, line, severity, message, rule_id }
  plugin_quality:scores        { round, scores, overall }
  plugin_quality:round_done    { round, scores }
  plugin_quality:converged     { round, final_score, message }

Convergence: 3 stable rounds with overall >= 90 and zero new high-severity findings.

By design this team only *reports* — it never auto-edits plugin or sandbox code.
Security-critical paths require human review.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_SD_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGINS_DIR = os.path.join(_SD_ROOT, "agent", "plugins")
_SHADOWDEV_DIR = os.path.join(_SD_ROOT, "shadowdev")
_TESTS_DIR = os.path.join(_SD_ROOT, "tests", "plugins")

DOMAINS: list[str] = [
    "sandbox_safety",
    "supply_chain",
    "rpc_reliability",
    "test_coverage",
]

_CONVERGENCE_STABLE_ROUNDS = 3
_CONVERGENCE_MIN_SCORE = 90
_ROUND_SLEEP_S = 60
_SEV_WEIGHT = {"high": 12, "medium": 4, "low": 1}


# --------------------------------------------------------------------- helpers


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _issue(domain: str, file: str, line: int, severity: str, rule: str, message: str) -> dict:
    return {
        "domain": domain, "file": file, "line": line,
        "severity": severity, "rule_id": rule, "message": message,
    }


def _score(issues: list[dict]) -> int:
    penalty = sum(_SEV_WEIGHT.get(i["severity"], 1) for i in issues)
    return max(0, 100 - penalty)


# --------------------------------------------------------------------- domains


def _scan_sandbox_safety() -> list[dict]:
    """Verify plugin_host._install_gates patches every known bypass path."""
    issues: list[dict] = []
    path = os.path.join(_SHADOWDEV_DIR, "plugin_host.py")
    src = _read(path)
    if not src:
        return [_issue("sandbox_safety", path, 0, "high", "PH-MISSING",
                       "plugin_host.py is missing")]

    required = {
        "socket.socket.connect":     "PH-001",
        "socket.socket.connect_ex":  "PH-002",
        "socket.create_connection":  "PH-003",
        "socket.getaddrinfo":        "PH-004",
        "builtins.open":             "PH-005",
        "os.open":                   "PH-006",
        "_subprocess.Popen":         "PH-007",
    }
    for symbol, rule in required.items():
        if symbol not in src:
            issues.append(_issue(
                "sandbox_safety", path, 0, "high", rule,
                f"sandbox gate missing: no patch for {symbol}",
            ))
    # Detect empty allowlist defaults that would imply 'allow everything'.
    if "_ALLOW_NET" in src and re.search(r"_ALLOW_NET\s*=\s*\[\s*\"\*\"\s*\]", src):
        issues.append(_issue(
            "sandbox_safety", path, 0, "medium", "PH-NET-WILD",
            "_ALLOW_NET initialised to wildcard — fail-open default",
        ))
    return issues


def _scan_supply_chain() -> list[dict]:
    """Installer must: sha256, signature path, compressed+uncompressed caps, filter='data'."""
    issues: list[dict] = []
    path = os.path.join(_PLUGINS_DIR, "installer.py")
    src = _read(path)
    if not src:
        return [_issue("supply_chain", path, 0, "high", "INST-MISSING",
                       "installer.py missing")]

    checks = [
        ("hashlib.sha256",            "INST-001", "high",   "no SHA256 verification"),
        ("_verify_signature",         "INST-002", "medium", "no signature verification helper"),
        ("_MAX_ARTIFACT_BYTES",       "INST-003", "high",   "compressed-artifact size cap missing"),
        ("_MAX_UNCOMPRESSED_BYTES",   "INST-004", "high",   "zip-bomb (uncompressed) cap missing"),
        ('filter="data"',             "INST-005", "medium", "tar.extractall not using filter='data'"),
        ("symlink rejected",          "INST-006", "medium", "symlinks not explicitly rejected"),
        ('".." in Path',              "INST-007", "high",   "path-traversal check missing"),
    ]
    for needle, rule, sev, msg in checks:
        if needle not in src:
            issues.append(_issue("supply_chain", path, 0, sev, rule, msg))
    return issues


def _scan_rpc_reliability() -> list[dict]:
    """RuntimeSandbox: per-RPC timeout + stderr drain + IncompleteReadError handling."""
    issues: list[dict] = []
    path = os.path.join(_PLUGINS_DIR, "sandbox.py")
    src = _read(path)
    if not src:
        return [_issue("rpc_reliability", path, 0, "high", "SB-MISSING",
                       "sandbox.py missing")]

    checks = [
        ("_HANDSHAKE_TIMEOUT_S",     "SB-001", "high",   "handshake timeout constant missing"),
        ("_drain_stderr",            "SB-002", "high",   "stderr is not drained — pipe-full deadlock risk"),
        ("IncompleteReadError",      "SB-003", "medium", "no IncompleteReadError handling on RPC read"),
        ("_kill_proc",               "SB-004", "medium", "no centralised proc-kill helper"),
        ("call_timeout_s",           "SB-005", "high",   "per-call timeout missing"),
    ]
    for needle, rule, sev, msg in checks:
        if needle not in src:
            issues.append(_issue("rpc_reliability", path, 0, sev, rule, msg))
    return issues


def _scan_test_coverage() -> list[dict]:
    """Required test files for each subsystem area."""
    issues: list[dict] = []
    if not os.path.isdir(_TESTS_DIR):
        return [_issue("test_coverage", _TESTS_DIR, 0, "high", "TEST-MISSING",
                       "tests/plugins/ directory is missing")]

    required_tests = {
        "test_sandbox_boundaries.py": "TEST-001",
        "test_signature.py":          "TEST-002",
        "test_startup_sweep.py":      "TEST-003",
        "test_proxy_schema.py":       "TEST-004",
        "test_server_cors.py":        "TEST-005",
        "test_proxy_sync_invoke.py":  "TEST-006",
        "test_installer.py":          "TEST-007",
        "test_registry_db.py":        "TEST-008",
        "test_manager_integration.py": "TEST-009",
    }
    for fname, rule in required_tests.items():
        if not os.path.isfile(os.path.join(_TESTS_DIR, fname)):
            issues.append(_issue(
                "test_coverage", os.path.join(_TESTS_DIR, fname), 0,
                "medium", rule, f"required test file missing: {fname}",
            ))
    return issues


_SCANNERS = {
    "sandbox_safety":   _scan_sandbox_safety,
    "supply_chain":     _scan_supply_chain,
    "rpc_reliability":  _scan_rpc_reliability,
    "test_coverage":    _scan_test_coverage,
}


# --------------------------------------------------------------------- team


class PluginQualityTeam:
    """Agent team that continuously audits the plugin subsystem.

    Read-only by design — it surfaces issues and lets humans fix them.
    Pair with the existing PluginManager for runtime concerns.
    """

    def __init__(self, sio: Any = None) -> None:
        self.sio = sio
        self.running: bool = False
        self.converged: bool = False
        self.round: int = 0
        self.scores: dict[str, int] = {d: 0 for d in DOMAINS}
        self.issues_by_domain: dict[str, list[dict]] = {d: [] for d in DOMAINS}
        self._stop_event = asyncio.Event()
        self._stable_rounds: int = 0

    async def get_status(self) -> dict:
        return {
            "running": self.running,
            "converged": self.converged,
            "round": self.round,
            "overall_score": self._overall_score(),
            "total_issues": sum(len(v) for v in self.issues_by_domain.values()),
            "scores": dict(self.scores),
            "issues": {d: list(v[:25]) for d, v in self.issues_by_domain.items()},
        }

    async def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    async def run_until_converged(self) -> None:
        self.running = True
        self.converged = False
        self._stop_event.clear()
        logger.info("PluginQualityTeam: starting plugin quality loop")

        try:
            while not self._stop_event.is_set() and not self.converged:
                self.round += 1
                await self._emit(
                    "plugin_quality:round_start",
                    {"round": self.round, "timestamp": datetime.now(timezone.utc).isoformat()},
                )

                for domain in DOMAINS:
                    await self._emit("plugin_quality:scanning", {"domain": domain})
                    issues = await asyncio.to_thread(_SCANNERS[domain])
                    self.issues_by_domain[domain] = issues
                    self.scores[domain] = _score(issues)
                    sev = {"high": 0, "medium": 0, "low": 0}
                    for i in issues:
                        sev[i["severity"]] = sev.get(i["severity"], 0) + 1
                    await self._emit(
                        "plugin_quality:finding",
                        {"domain": domain, "issues_found": len(issues), "severity_breakdown": sev},
                    )
                    for i in issues[:25]:
                        await self._emit("plugin_quality:issue", i)

                overall = self._overall_score()
                await self._emit(
                    "plugin_quality:scores",
                    {"round": self.round, "scores": dict(self.scores), "overall": overall},
                )
                await self._emit(
                    "plugin_quality:round_done",
                    {"round": self.round, "scores": dict(self.scores)},
                )

                # Convergence: 3 stable rounds at >= 90 with zero highs.
                highs = sum(
                    1 for v in self.issues_by_domain.values() for i in v if i["severity"] == "high"
                )
                if highs == 0 and overall >= _CONVERGENCE_MIN_SCORE:
                    self._stable_rounds += 1
                else:
                    self._stable_rounds = 0

                if self._stable_rounds >= _CONVERGENCE_STABLE_ROUNDS:
                    self.converged = True
                    await self._emit(
                        "plugin_quality:converged",
                        {"round": self.round, "final_score": overall,
                         "message": f"Converged at {overall}/100 after "
                                    f"{self._stable_rounds} stable rounds"},
                    )
                    logger.info("PluginQualityTeam: converged at round %d (score %d)",
                                self.round, overall)
                    break

                # Sleep between rounds; honor stop
                for _ in range(_ROUND_SLEEP_S):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("PluginQualityTeam: cancelled")
            raise
        finally:
            self.running = False

    def _overall_score(self) -> int:
        if not DOMAINS:
            return 0
        return round(sum(self.scores.values()) / len(DOMAINS))

    async def _emit(self, event: str, data: dict) -> None:
        if self.sio is None:
            return
        try:
            await self.sio.emit(event, data)
        except Exception as e:
            logger.debug("PluginQualityTeam emit %s failed: %s", event, e)
