"""
Diagnostics tool — system health check for the ShadowDev environment.
Inspired by `claude doctor` from the CC reference.

Checks: Python version, required packages, API key presence,
workspace accessibility, git availability, MCP servers, Docker sandbox.
"""
import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

import config

logger = logging.getLogger(__name__)

_REQUIRED_PACKAGES = [
    "langchain_core",
    "langgraph",
    "pydantic",
    "rich",
]

_OPTIONAL_PACKAGES = {
    "langchain_anthropic": "Anthropic provider",
    "langchain_openai": "OpenAI provider",
    "langchain_google_genai": "Google Gemini provider",
    "langchain_groq": "Groq provider",
    "textual": "TUI interface",
    "plyer": "Desktop notifications",
    "docker": "Docker sandbox",
    "ripgrep": "Fast code search (rg)",
}


def _check(label: str, ok: bool, detail: str = "") -> dict:
    return {"label": label, "ok": ok, "detail": detail}


@tool
def diagnostics() -> str:
    """Run a system health check and report the status of all ShadowDev components.

    Checks: Python version, required packages, API keys, workspace access,
    git availability, MCP server config, and optional features.

    Use this when troubleshooting setup issues or verifying the environment
    before starting a new session.
    """
    results: list[dict] = []

    # ── Python version ─────────────────────────────────────────────────────
    version = sys.version_info
    ok = version >= (3, 11)
    results.append(_check(
        "Python version",
        ok,
        f"{version.major}.{version.minor}.{version.micro} "
        f"({'OK' if ok else 'requires 3.11+'})"
    ))

    # ── Required packages ──────────────────────────────────────────────────
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append(_check(f"Package: {pkg}", True, "installed"))
        except ImportError:
            results.append(_check(f"Package: {pkg}", False, "MISSING — run: pip install shadowdev"))

    # ── Optional packages ──────────────────────────────────────────────────
    for pkg, desc in _OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(pkg.replace("-", "_"))
            results.append(_check(f"Optional: {pkg}", True, desc))
        except ImportError:
            results.append(_check(f"Optional: {pkg}", None, f"{desc} — not installed"))

    # ── API keys (presence only, never log values) ─────────────────────────
    provider = getattr(config, "LLM_PROVIDER", "")
    key_map = {
        "anthropic": ("ANTHROPIC_API_KEY", getattr(config, "ANTHROPIC_API_KEY", "")),
        "openai":    ("OPENAI_API_KEY",    getattr(config, "OPENAI_API_KEY", "")),
        "google":    ("GOOGLE_API_KEY",    getattr(config, "GOOGLE_API_KEY", "")),
        "groq":      ("GROQ_API_KEY",      getattr(config, "GROQ_API_KEY", "")),
    }
    if provider in key_map:
        key_name, key_val = key_map[provider]
        has_key = bool(key_val and key_val.strip())
        results.append(_check(
            f"API key: {key_name}",
            has_key,
            "present" if has_key else f"MISSING — set {key_name} in .env",
        ))

    # ── Workspace ──────────────────────────────────────────────────────────
    ws = getattr(config, "WORKSPACE_DIR", "")
    if ws and os.path.isdir(ws):
        writable = os.access(ws, os.W_OK)
        results.append(_check("Workspace", writable, f"{ws} ({'writable' if writable else 'READ-ONLY'})"))
    else:
        results.append(_check("Workspace", False, f"'{ws}' does not exist"))

    # ── .shadowdev directory ───────────────────────────────────────────────
    shadowdev_dir = Path(ws) / ".shadowdev" if ws else None
    if shadowdev_dir:
        exists = shadowdev_dir.exists()
        results.append(_check(
            ".shadowdev dir",
            exists,
            str(shadowdev_dir) if exists else "will be created on first use",
        ))

    # ── Git availability ───────────────────────────────────────────────────
    try:
        out = subprocess.check_output(
            ["git", "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        results.append(_check("Git", True, out))
    except Exception:
        results.append(_check("Git", False, "git not found in PATH"))

    # ── Is workspace a git repo ────────────────────────────────────────────
    if ws:
        try:
            subprocess.check_call(
                ["git", "rev-parse", "--git-dir"],
                cwd=ws, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
            results.append(_check("Git repo", True, "workspace is a git repository"))
        except Exception:
            results.append(_check("Git repo", None, "workspace is not a git repository"))

    # ── MCP servers ────────────────────────────────────────────────────────
    mcp_servers = getattr(config, "MCP_SERVERS", {})
    if mcp_servers:
        results.append(_check("MCP servers", True, f"{len(mcp_servers)} configured: {', '.join(mcp_servers.keys())}"))
    else:
        results.append(_check("MCP servers", None, "none configured (optional)"))

    # ── Docker sandbox ─────────────────────────────────────────────────────
    sandbox_enabled = getattr(config, "SANDBOX_ENABLED", False)
    if sandbox_enabled:
        try:
            subprocess.check_output(
                ["docker", "info"], stderr=subprocess.DEVNULL, timeout=5
            )
            results.append(_check("Docker sandbox", True, "Docker available and sandbox enabled"))
        except Exception:
            results.append(_check(
                "Docker sandbox", False,
                "SANDBOX_ENABLED=True but Docker is not running — commands run unsandboxed"
            ))
    else:
        results.append(_check("Docker sandbox", None, "disabled (SANDBOX_ENABLED=False)"))

    # ── Format output ──────────────────────────────────────────────────────
    ok_icon = "✅"
    warn_icon = "⚠️ "
    fail_icon = "❌"

    lines = ["## ShadowDev Diagnostics\n"]
    pass_count = sum(1 for r in results if r["ok"] is True)
    fail_count = sum(1 for r in results if r["ok"] is False)
    warn_count = sum(1 for r in results if r["ok"] is None)

    lines.append(f"**{pass_count} passed  {fail_count} failed  {warn_count} warnings**\n")

    for r in results:
        if r["ok"] is True:
            icon = ok_icon
        elif r["ok"] is False:
            icon = fail_icon
        else:
            icon = warn_icon
        detail = f"  — {r['detail']}" if r["detail"] else ""
        lines.append(f"{icon} {r['label']}{detail}")

    if fail_count > 0:
        lines.append(
            "\n**Action required:** Fix the ❌ items above before starting work. "
            "Run `pip install -e .` to install missing packages."
        )
    elif warn_count > 0:
        lines.append("\n**Optional:** ⚠️  items are not required but may improve functionality.")
    else:
        lines.append("\n**All checks passed!** ShadowDev is ready.")

    return "\n".join(lines)
