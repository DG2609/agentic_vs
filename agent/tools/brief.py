"""
Brief tool — multi-file context summarization for session context.
Inspired by Claude Code's BriefTool.

Reads multiple files and produces a concise summary suitable for
adding as context at the start of a session or sharing with a subagent.
"""
import logging
import os
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path

logger = logging.getLogger(__name__)

_MAX_FILES = 20
_MAX_FILE_BYTES = 50_000  # 50KB per file
_MAX_TOTAL_BYTES = 200_000  # 200KB total before summarization


class BriefArgs(BaseModel):
    paths: list[str] = Field(
        description=(
            "List of file paths (relative to workspace) to include in the brief. "
            "Supports globs like 'src/**/*.py'. Max 20 files."
        )
    )
    focus: str = Field(
        default="",
        description=(
            "Optional focus area: what aspects to highlight in the summary. "
            "Examples: 'public API', 'error handling', 'database schema'."
        )
    )
    max_lines_per_file: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Max lines to include per file (default 100). Truncated files get a note.",
    )


@tool(args_schema=BriefArgs)
def brief(paths: list[str], focus: str = "", max_lines_per_file: int = 100) -> str:
    """Summarize multiple files into a concise context brief.

    Use this to build context before delegating to a subagent, starting a new
    session, or when you need a compact overview of several related files.

    Args:
        paths: List of file paths or globs (relative to workspace). Max 20 files.
        focus: Optional focus area to emphasize in summaries.
        max_lines_per_file: Max lines per file before truncating.
    """
    import glob as _glob

    if not paths:
        return "Error: no paths provided. Pass at least one file path or glob pattern."

    # Expand globs
    resolved_paths: list[str] = []
    for p in paths:
        resolved = resolve_tool_path(p)
        if resolved is None:
            continue
        if "*" in resolved or "?" in resolved:
            matches = _glob.glob(resolved, recursive=True)
            resolved_paths.extend(matches[:_MAX_FILES])
        else:
            resolved_paths.append(resolved)

    resolved_paths = resolved_paths[:_MAX_FILES]
    if not resolved_paths:
        return f"Error: no files found matching: {', '.join(paths)}"

    total_bytes = 0
    sections: list[str] = []
    skipped: list[str] = []

    for fpath in resolved_paths:
        p = Path(fpath)
        if not p.exists():
            skipped.append(f"{fpath} (not found)")
            continue
        if not p.is_file():
            skipped.append(f"{fpath} (not a file)")
            continue

        size = p.stat().st_size
        if size == 0:
            skipped.append(f"{fpath} (empty)")
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            skipped.append(f"{fpath} ({e})")
            continue

        lines = content.splitlines()
        truncated = len(lines) > max_lines_per_file
        if truncated:
            lines = lines[:max_lines_per_file]

        excerpt = "\n".join(lines)
        excerpt_bytes = len(excerpt.encode("utf-8"))

        if total_bytes + excerpt_bytes > _MAX_TOTAL_BYTES:
            skipped.append(f"{fpath} (total limit reached)")
            break

        total_bytes += excerpt_bytes

        # Relative path for display
        try:
            rel = os.path.relpath(fpath, config.WORKSPACE_DIR).replace("\\", "/")
        except ValueError:
            rel = fpath

        header = f"### {rel}"
        if truncated:
            header += f"  *(first {max_lines_per_file} of {len(content.splitlines())} lines)*"
        sections.append(f"{header}\n```\n{excerpt}\n```")

    if not sections:
        return f"Brief: no readable files found.\nSkipped: {', '.join(skipped)}"

    focus_line = f"\n**Focus: {focus}**\n" if focus else ""
    header = (
        f"# Context Brief\n"
        f"{focus_line}"
        f"**Files: {len(sections)}** | "
        f"**Total: {total_bytes // 1024}KB**\n"
    )
    if skipped:
        header += f"**Skipped:** {', '.join(skipped)}\n"

    result = header + "\n" + "\n\n".join(sections)
    return truncate_output(result)
