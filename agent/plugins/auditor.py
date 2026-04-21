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

        for field in ("name", "version", "tools", "permissions"):
            if field not in data:
                blockers.append(QualityIssue(
                    rule="invalid-manifest",
                    message=f"plugin.json missing field '{field}'",
                    severity="high",
                ))

        for p in data.get("permissions", []):
            base = p.split("=")[0] if isinstance(p, str) else str(p)
            if base not in _ALLOWED_PERMS:
                blockers.append(QualityIssue(
                    rule="unknown-permission",
                    message=f"unknown permission '{p}' (allowed: {sorted(_ALLOWED_PERMS)})",
                    severity="high",
                ))

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

        raw["ruff"] = await self._run_if_present("ruff", ["check", "--output-format", "json", str(pdir)])
        raw["bandit"] = await self._run_if_present("bandit", ["-r", str(pdir), "-f", "json", "-q"])
        raw["mypy"] = await self._run_if_present("mypy", ["--no-error-summary", "--hide-error-context", str(pdir)])

        self._issues_from_ruff(raw.get("ruff", {}), issues)
        self._issues_from_bandit(raw.get("bandit", {}), issues, blockers)

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
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""
