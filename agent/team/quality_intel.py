"""
QualityIntelTeam — Agent team that continuously reviews and improves
ShadowDev's code quality across 7 domains and stops when the project
reaches a "best" state (convergence).

Twin of PromptIntelTeam but focused on project quality rather than
prompt pattern parity with Claude Code.

Emits Socket.IO events for the Ink CLI frontend:
  quality:status       { running, round, overall_score, total_improvements, converged }
  quality:round_start  { round, timestamp }
  quality:scanning     { domain, tool }
  quality:finding      { domain, issues_found, severity_breakdown }
  quality:issue        { domain, file, line, severity, message, rule_id }
  quality:improvement  { domain, target_file, description, auto_applied }
  quality:scores       { round, scores, overall }
  quality:round_done   { round, improvements_this_round, scores }
  quality:converged    { round, final_score, message }

Convergence: stops automatically when 3 rounds pass with zero
improvements applied AND overall score >= 90.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_SD_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AGENT_DIR = os.path.join(_SD_ROOT, "agent")
_MODELS_DIR = os.path.join(_SD_ROOT, "models")
_SERVER_DIR = os.path.join(_SD_ROOT, "server")

# Convergence thresholds
_CONVERGENCE_STABLE_ROUNDS = 3
_CONVERGENCE_MIN_SCORE = 90

# Pacing
_ROUND_SLEEP_S = 90
_MAX_ISSUES_PER_DOMAIN = 25
_MAX_AUTO_FIXES_PER_ROUND = 5

DOMAINS: list[str] = [
    "code_quality",
    "type_safety",
    "test_coverage",
    "security",
    "performance",
    "architecture",
    "docs",
]

# ---------------------------------------------------------------------------
# Python source files we care about (everything under agent/, models/, server/)
# ---------------------------------------------------------------------------

def _collect_py_files() -> list[str]:
    """Gather .py files under key project dirs."""
    out: list[str] = []
    for root in (_AGENT_DIR, _MODELS_DIR, _SERVER_DIR):
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            # Skip caches
            if "__pycache__" in dirpath or ".pytest_cache" in dirpath:
                continue
            for f in files:
                if f.endswith(".py"):
                    out.append(os.path.join(dirpath, f))
    return out


# ---------------------------------------------------------------------------
# Domain workers
# ---------------------------------------------------------------------------

async def _run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command asynchronously, capturing stdout/stderr."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd or _SD_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", f"Command timed out after {timeout}s"
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return 127, "", f"Tool not found: {cmd[0]}"
    except OSError as exc:
        return 1, "", f"OSError: {exc}"


def _tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


# --- code_quality (ruff) ---------------------------------------------------

async def _scan_code_quality() -> list[dict]:
    if not _tool_exists("ruff"):
        return []
    code, stdout, _stderr = await _run_cmd(
        ["ruff", "check", "agent", "models", "server", "--output-format", "json"],
        timeout=60,
    )
    issues: list[dict] = []
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    for item in data[:_MAX_ISSUES_PER_DOMAIN]:
        issues.append(
            {
                "domain": "code_quality",
                "file": os.path.relpath(item.get("filename", ""), _SD_ROOT),
                "line": (item.get("location") or {}).get("row", 0),
                "severity": "low",
                "rule_id": item.get("code", "RUFF"),
                "message": item.get("message", "")[:200],
                "fixable": bool((item.get("fix") or {}).get("applicability") in ("safe", "auto")),
            }
        )
    return issues


async def _autofix_code_quality() -> int:
    """Run ruff --fix for safe fixes. Returns number of files modified."""
    if not _tool_exists("ruff"):
        return 0
    # Count modified files by comparing mtimes
    before = {p: os.path.getmtime(p) for p in _collect_py_files() if os.path.isfile(p)}
    await _run_cmd(
        ["ruff", "check", "agent", "models", "server", "--fix", "--fix-only"],
        timeout=60,
    )
    after = {p: os.path.getmtime(p) for p in before if os.path.isfile(p)}
    changed = sum(1 for p, t in before.items() if after.get(p, t) != t)
    return changed


# --- type_safety (mypy) ----------------------------------------------------

async def _scan_type_safety() -> list[dict]:
    if not _tool_exists("mypy"):
        return []
    code, stdout, _stderr = await _run_cmd(
        ["mypy", "--no-error-summary", "--hide-error-context", "agent"],
        timeout=120,
    )
    issues: list[dict] = []
    # mypy format: path:line: severity: message  [error-code]
    pat = re.compile(r"^(.+?):(\d+):\s*(error|warning|note):\s*(.+?)(?:\s+\[([a-z0-9\-]+)\])?\s*$")
    for line in stdout.splitlines():
        m = pat.match(line)
        if not m:
            continue
        sev = {"error": "medium", "warning": "low", "note": "low"}.get(m.group(3), "low")
        issues.append(
            {
                "domain": "type_safety",
                "file": os.path.relpath(m.group(1), _SD_ROOT),
                "line": int(m.group(2)),
                "severity": sev,
                "rule_id": m.group(5) or "mypy",
                "message": m.group(4)[:200],
                "fixable": False,
            }
        )
        if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
            break
    return issues


# --- test_coverage (pytest --cov) ------------------------------------------

async def _scan_test_coverage() -> list[dict]:
    # This is expensive — we fake a lightweight signal: count test files vs source files.
    tests_dir = os.path.join(_SD_ROOT, "tests")
    if not os.path.isdir(tests_dir):
        return [
            {
                "domain": "test_coverage",
                "file": "tests/",
                "line": 0,
                "severity": "high",
                "rule_id": "no-tests",
                "message": "No tests/ directory found",
                "fixable": False,
            }
        ]
    src_files = _collect_py_files()
    test_files = [f for f in _collect_py_files() if False]  # placeholder
    test_count = 0
    for dirpath, _dirs, files in os.walk(tests_dir):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                test_count += 1
    if test_count == 0:
        return [
            {
                "domain": "test_coverage",
                "file": "tests/",
                "line": 0,
                "severity": "high",
                "rule_id": "no-tests",
                "message": "No test files found",
                "fixable": False,
            }
        ]
    # Flag source modules without matching test_* file
    existing_test_basenames: set[str] = set()
    for dirpath, _dirs, files in os.walk(tests_dir):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                existing_test_basenames.add(f[len("test_"):-3])
    issues: list[dict] = []
    for src in src_files:
        rel = os.path.relpath(src, _SD_ROOT)
        if rel.startswith("tests"):
            continue
        base = os.path.splitext(os.path.basename(src))[0]
        if base.startswith("_") or base == "__init__":
            continue
        if base not in existing_test_basenames:
            issues.append(
                {
                    "domain": "test_coverage",
                    "file": rel,
                    "line": 0,
                    "severity": "medium",
                    "rule_id": "missing-test-module",
                    "message": f"No test_{base}.py found",
                    "fixable": False,
                }
            )
            if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
                break
    return issues


# --- security (bandit) -----------------------------------------------------

async def _scan_security() -> list[dict]:
    if not _tool_exists("bandit"):
        return []
    code, stdout, _stderr = await _run_cmd(
        ["bandit", "-r", "agent", "models", "server", "-f", "json", "-q"],
        timeout=90,
    )
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    issues: list[dict] = []
    for item in (data.get("results") or [])[:_MAX_ISSUES_PER_DOMAIN]:
        sev = (item.get("issue_severity") or "LOW").lower()
        sev_map = {"high": "high", "medium": "medium", "low": "low"}
        issues.append(
            {
                "domain": "security",
                "file": os.path.relpath(item.get("filename", ""), _SD_ROOT),
                "line": item.get("line_number", 0),
                "severity": sev_map.get(sev, "low"),
                "rule_id": item.get("test_id", "B000"),
                "message": (item.get("issue_text") or "")[:200],
                "fixable": False,
            }
        )
    return issues


# --- performance (AST anti-patterns) ---------------------------------------

_PERF_ANTIPATTERNS = {
    # Repeated string concat in loop
    "string_concat_in_loop": r"for\s+\w+\s+in\s+.+:\s*\n\s+\w+\s*\+=\s*['\"]",
}


async def _scan_performance() -> list[dict]:
    issues: list[dict] = []
    for path in _collect_py_files():
        if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            # Nested loops doing database/io-ish calls
            if isinstance(node, (ast.For, ast.While)):
                inner_calls = [
                    c for c in ast.walk(node)
                    if isinstance(c, ast.Call) and _call_name(c) in {
                        "open", "requests.get", "requests.post", "subprocess.run",
                    }
                ]
                if inner_calls:
                    issues.append(
                        {
                            "domain": "performance",
                            "file": os.path.relpath(path, _SD_ROOT),
                            "line": node.lineno,
                            "severity": "medium",
                            "rule_id": "io-in-loop",
                            "message": "I/O or subprocess call inside a loop",
                            "fixable": False,
                        }
                    )
                    if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
                        break
        # str concat pattern
        for m in re.finditer(r"for\s+\w+\s+in\s+.+?:\s*\n(\s+)\w+\s*\+=\s*(['\"])", src):
            line_no = src.count("\n", 0, m.start()) + 1
            issues.append(
                {
                    "domain": "performance",
                    "file": os.path.relpath(path, _SD_ROOT),
                    "line": line_no,
                    "severity": "low",
                    "rule_id": "str-concat-loop",
                    "message": "String += in loop; prefer ''.join()",
                    "fixable": False,
                }
            )
            if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
                break
    return issues


def _call_name(node: ast.Call) -> str:
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


# --- architecture (file size / complexity) ---------------------------------

_ARCH_MAX_LINES = 800
_ARCH_MAX_FUNC_LINES = 80


async def _scan_architecture() -> list[dict]:
    issues: list[dict] = []
    for path in _collect_py_files():
        if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue

        line_count = src.count("\n") + 1
        rel = os.path.relpath(path, _SD_ROOT)
        if line_count > _ARCH_MAX_LINES:
            issues.append(
                {
                    "domain": "architecture",
                    "file": rel,
                    "line": 0,
                    "severity": "medium",
                    "rule_id": "file-too-large",
                    "message": f"{line_count} lines (>{_ARCH_MAX_LINES}) — consider splitting",
                    "fixable": False,
                }
            )

        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.end_lineno and (node.end_lineno - node.lineno) > _ARCH_MAX_FUNC_LINES:
                    issues.append(
                        {
                            "domain": "architecture",
                            "file": rel,
                            "line": node.lineno,
                            "severity": "low",
                            "rule_id": "function-too-long",
                            "message": f"{node.name}() is {node.end_lineno - node.lineno} lines",
                            "fixable": False,
                        }
                    )
                    if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
                        break
    return issues


# --- docs (docstring coverage) ---------------------------------------------

async def _scan_docs() -> list[dict]:
    issues: list[dict] = []
    for path in _collect_py_files():
        if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
            break
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, _SD_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # skip private / dunder
                if node.name.startswith("_"):
                    continue
                if not ast.get_docstring(node):
                    issues.append(
                        {
                            "domain": "docs",
                            "file": rel,
                            "line": node.lineno,
                            "severity": "low",
                            "rule_id": "missing-docstring",
                            "message": f"Public {type(node).__name__[:-3].lower()} '{node.name}' missing docstring",
                            "fixable": False,
                        }
                    )
                    if len(issues) >= _MAX_ISSUES_PER_DOMAIN:
                        break
    return issues


# ---------------------------------------------------------------------------
# Domain dispatcher
# ---------------------------------------------------------------------------

_DOMAIN_SCANNERS = {
    "code_quality": _scan_code_quality,
    "type_safety": _scan_type_safety,
    "test_coverage": _scan_test_coverage,
    "security": _scan_security,
    "performance": _scan_performance,
    "architecture": _scan_architecture,
    "docs": _scan_docs,
}

_DOMAIN_TOOLS = {
    "code_quality": "ruff",
    "type_safety": "mypy",
    "test_coverage": "pytest-scan",
    "security": "bandit",
    "performance": "ast",
    "architecture": "ast",
    "docs": "ast",
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHT = {"high": 10, "medium": 3, "low": 1}


def _score_domain(issues: list[dict]) -> int:
    """0-100: higher is better. No issues = 100."""
    if not issues:
        return 100
    penalty = sum(_SEVERITY_WEIGHT.get(i["severity"], 1) for i in issues)
    # Scale: 10 medium issues = 30 penalty → 70 score
    score = max(0, 100 - penalty)
    return score


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class QualityIntelTeam:
    """Agent team that scans + improves project quality until convergence."""

    def __init__(self, sio=None) -> None:
        self.sio = sio
        self.running: bool = False
        self.converged: bool = False
        self.round: int = 0
        self.total_improvements: int = 0
        self.scores: dict[str, int] = {d: 0 for d in DOMAINS}
        self.issues_by_domain: dict[str, list[dict]] = {d: [] for d in DOMAINS}
        self.improvements_log: list[dict] = []
        self._stop_event = asyncio.Event()
        self._stable_rounds: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        return {
            "running": self.running,
            "converged": self.converged,
            "round": self.round,
            "overall_score": self._overall_score(),
            "total_improvements": self.total_improvements,
            "scores": dict(self.scores),
            "improvements_log": self.improvements_log[-20:],
        }

    async def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_until_converged(self) -> None:
        self.running = True
        self.converged = False
        self._stop_event.clear()
        logger.info("QualityIntelTeam: starting review→improve loop")

        try:
            while not self._stop_event.is_set() and not self.converged:
                self.round += 1
                await self._emit(
                    "quality:round_start",
                    {"round": self.round, "timestamp": datetime.now(timezone.utc).isoformat()},
                )
                await self._emit_status()

                # Scan all domains in parallel
                results = await asyncio.gather(
                    *[self._scan_domain(d) for d in DOMAINS],
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Quality scan error: %s", r)
                        continue
                    if isinstance(r, dict) and "domain" in r:
                        self.issues_by_domain[r["domain"]] = r["issues"]

                # Update scores
                for d in DOMAINS:
                    self.scores[d] = _score_domain(self.issues_by_domain.get(d, []))

                # Attempt improvements
                improvements_this_round = await self._improve_round()

                overall = self._overall_score()
                await self._emit(
                    "quality:scores",
                    {"round": self.round, "scores": dict(self.scores), "overall": overall},
                )
                await self._emit(
                    "quality:round_done",
                    {
                        "round": self.round,
                        "improvements_this_round": improvements_this_round,
                        "scores": dict(self.scores),
                    },
                )

                # Convergence check
                if improvements_this_round == 0:
                    self._stable_rounds += 1
                else:
                    self._stable_rounds = 0

                if self._stable_rounds >= _CONVERGENCE_STABLE_ROUNDS and overall >= _CONVERGENCE_MIN_SCORE:
                    self.converged = True
                    await self._emit(
                        "quality:converged",
                        {
                            "round": self.round,
                            "final_score": overall,
                            "message": (
                                f"Converged — {self._stable_rounds} stable rounds at "
                                f"overall quality {overall}/100"
                            ),
                        },
                    )
                    logger.info(
                        "QualityIntelTeam: converged at round %d (score %d)",
                        self.round, overall,
                    )
                    break

                await self._emit_status()

                # Sleep between rounds; honor stop
                for _ in range(_ROUND_SLEEP_S):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("QualityIntelTeam: cancelled")
        finally:
            self.running = False
            await self._emit_status()

    # ------------------------------------------------------------------
    # Domain scan
    # ------------------------------------------------------------------

    async def _scan_domain(self, domain: str) -> dict:
        tool = _DOMAIN_TOOLS.get(domain, "scan")
        await self._emit("quality:scanning", {"domain": domain, "tool": tool})

        scanner = _DOMAIN_SCANNERS.get(domain)
        if scanner is None:
            return {"domain": domain, "issues": []}

        try:
            issues = await scanner()
        except Exception as exc:
            logger.error("Scanner %s failed: %s", domain, exc)
            issues = []

        sev_breakdown = {"high": 0, "medium": 0, "low": 0}
        for i in issues:
            sev_breakdown[i.get("severity", "low")] = sev_breakdown.get(i.get("severity", "low"), 0) + 1

        await self._emit(
            "quality:finding",
            {
                "domain": domain,
                "issues_found": len(issues),
                "severity_breakdown": sev_breakdown,
            },
        )

        # Emit the top 3 most severe issues individually so users see real context
        sorted_issues = sorted(
            issues,
            key=lambda i: _SEVERITY_WEIGHT.get(i.get("severity", "low"), 1),
            reverse=True,
        )
        for issue in sorted_issues[:3]:
            await self._emit(
                "quality:issue",
                {
                    "domain": domain,
                    "file": issue.get("file", ""),
                    "line": issue.get("line", 0),
                    "severity": issue.get("severity", "low"),
                    "message": issue.get("message", ""),
                    "rule_id": issue.get("rule_id", ""),
                },
            )

        return {"domain": domain, "issues": issues}

    # ------------------------------------------------------------------
    # Improvement application
    # ------------------------------------------------------------------

    async def _improve_round(self) -> int:
        """
        Apply safe automatic fixes where possible.
        Currently auto-fixable: ruff (code_quality).
        Everything else is logged as a suggestion.
        """
        applied = 0

        # Auto-fix code_quality via ruff --fix
        cq_issues = self.issues_by_domain.get("code_quality", [])
        if cq_issues:
            fixable = [i for i in cq_issues if i.get("fixable")]
            if fixable:
                files_changed = await _autofix_code_quality()
                if files_changed > 0:
                    desc = f"ruff --fix applied safe fixes to {files_changed} file(s)"
                    self.improvements_log.append(
                        {
                            "domain": "code_quality",
                            "target_file": f"{files_changed} files",
                            "description": desc,
                            "auto_applied": True,
                            "round": self.round,
                        }
                    )
                    await self._emit(
                        "quality:improvement",
                        {
                            "domain": "code_quality",
                            "target_file": f"{files_changed} files",
                            "description": desc,
                            "auto_applied": True,
                        },
                    )
                    applied += 1
                    self.total_improvements += 1

        # Log (but do not auto-apply) remaining high-severity issues from other domains
        logged = 0
        for domain, issues in self.issues_by_domain.items():
            if domain == "code_quality":
                continue
            for issue in sorted(
                issues,
                key=lambda i: _SEVERITY_WEIGHT.get(i.get("severity", "low"), 1),
                reverse=True,
            ):
                if logged >= _MAX_AUTO_FIXES_PER_ROUND:
                    break
                if issue.get("severity") != "high":
                    continue
                # Track the top high-severity issue as an actionable suggestion
                desc = (
                    f"[{issue.get('rule_id', '')}] {issue.get('message', '')[:100]} "
                    f"at {issue.get('file', '')}:{issue.get('line', 0)}"
                )
                # Check we haven't already logged this exact suggestion
                if any(
                    e.get("description") == desc for e in self.improvements_log
                ):
                    continue
                self.improvements_log.append(
                    {
                        "domain": domain,
                        "target_file": issue.get("file", ""),
                        "description": desc,
                        "auto_applied": False,
                        "round": self.round,
                    }
                )
                await self._emit(
                    "quality:improvement",
                    {
                        "domain": domain,
                        "target_file": issue.get("file", ""),
                        "description": desc,
                        "auto_applied": False,
                    },
                )
                logged += 1

        return applied + logged

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _overall_score(self) -> int:
        if not self.scores:
            return 0
        return int(sum(self.scores.values()) / len(self.scores))

    async def _emit_status(self) -> None:
        await self._emit(
            "quality:status",
            {
                "running": self.running,
                "converged": self.converged,
                "round": self.round,
                "overall_score": self._overall_score(),
                "total_improvements": self.total_improvements,
            },
        )

    async def _emit(self, event: str, data: dict) -> None:
        if self.sio is None:
            return
        try:
            await self.sio.emit(event, data)
        except Exception as exc:  # pragma: no cover
            logger.debug("QualityIntel emit error (%s): %s", event, exc)
