"""
AgentSummary tool — produce a compact snapshot of current agent state.
Inspired by CC's AgentSummary service.

Reports: session stats, active tools, pending todos, recent memory,
git context, and workspace overview. Useful before handing off to a
subagent or starting a new phase of work.
"""
import logging
import os
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

import config

logger = logging.getLogger(__name__)


class AgentSummaryArgs(BaseModel):
    include_todos: bool = Field(
        default=True,
        description="Include pending todos in the summary.",
    )
    include_git: bool = Field(
        default=True,
        description="Include git status in the summary.",
    )
    include_memory: bool = Field(
        default=True,
        description="Include recent memory entries in the summary.",
    )
    include_files: bool = Field(
        default=True,
        description="Include top-level workspace file listing.",
    )


@tool(args_schema=AgentSummaryArgs)
def agent_summary(
    include_todos: bool = True,
    include_git: bool = True,
    include_memory: bool = True,
    include_files: bool = True,
) -> str:
    """Produce a compact snapshot of current agent state and workspace context.

    Use before:
    - Handing off to a subagent or worker
    - Starting a new phase of work after a long session
    - Creating a status update for the user

    Returns a structured summary covering: workspace, git status, todos,
    recent memory, and session memory file.
    """
    sections: list[str] = ["# Agent State Summary\n"]

    # ── Workspace overview ─────────────────────────────────────────────────
    ws = config.WORKSPACE_DIR
    sections.append(f"**Workspace:** `{ws}`")
    sections.append(f"**Provider:** {getattr(config, 'LLM_PROVIDER', 'unknown')}  "
                    f"**Model:** {getattr(config, 'LLM_MODEL', 'unknown')}")

    # ── Top-level files ────────────────────────────────────────────────────
    if include_files:
        try:
            entries = sorted(os.listdir(ws))
            dirs = [e + "/" for e in entries if os.path.isdir(os.path.join(ws, e))
                    and not e.startswith(".") and e not in {"__pycache__", "node_modules"}]
            files = [e for e in entries if os.path.isfile(os.path.join(ws, e))
                     and not e.startswith(".")]
            top = (dirs + files)[:20]
            sections.append(f"\n**Workspace files (top 20):** {', '.join(top)}"
                            + (f" (+{len(entries) - 20} more)" if len(entries) > 20 else ""))
        except OSError:
            pass

    # ── Git status ─────────────────────────────────────────────────────────
    if include_git:
        try:
            import subprocess
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=ws, stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
            status = subprocess.check_output(
                ["git", "status", "--short"],
                cwd=ws, stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
            log = subprocess.check_output(
                ["git", "log", "--oneline", "-3"],
                cwd=ws, stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
            status_display = status[:200] if status else "(clean)"
            sections.append(
                f"\n**Git branch:** `{branch}`\n"
                f"**Status:** {status_display}\n"
                f"**Recent commits:**\n```\n{log}\n```"
            )
        except Exception:
            sections.append("\n**Git:** (not a git repo or git unavailable)")

    # ── Pending todos ──────────────────────────────────────────────────────
    if include_todos:
        try:
            from agent.tools.todo import _get_todos
            todos = _get_todos()
            pending = [t for t in todos if t.get("status") not in ("completed", "cancelled")]
            if pending:
                lines = [f"\n**Pending todos ({len(pending)}):**"]
                for t in pending[:10]:
                    lines.append(f"  - [{t.get('status','?')}] {t.get('content','')}")
                if len(pending) > 10:
                    lines.append(f"  ... and {len(pending) - 10} more")
                sections.append("\n".join(lines))
            else:
                sections.append("\n**Todos:** (none pending)")
        except Exception:
            pass

    # ── Recent memory ──────────────────────────────────────────────────────
    if include_memory:
        try:
            from agent.tools.memory import memory_search
            recent = memory_search.invoke({"query": "", "limit": 5})
            if recent and "No memories" not in str(recent):
                sections.append(f"\n**Recent memory:**\n{str(recent)[:500]}")
        except Exception:
            pass

    # ── Session memory file ────────────────────────────────────────────────
    try:
        from agent.auto_dream import get_memory_content
        mem = get_memory_content()
        if mem:
            preview = mem[:400].strip()
            sections.append(f"\n**Session memory (.shadowdev/session-memory.md):**\n```\n{preview}\n```")
    except Exception:
        pass

    # ── Config snapshot ────────────────────────────────────────────────────
    advisor = getattr(config, "ADVISOR_MODEL", "")
    undercover = getattr(config, "UNDERCOVER_MODE", False)
    coordinator = getattr(config, "COORDINATOR_MODE", False)
    auto_dream = getattr(config, "AUTO_DREAM_ENABLED", True)
    sections.append(
        f"\n**Config:** advisor={advisor or 'off'}  "
        f"undercover={undercover}  coordinator={coordinator}  "
        f"auto_dream={auto_dream}"
    )

    return "\n".join(sections)
