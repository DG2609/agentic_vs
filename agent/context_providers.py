"""
Context providers — parse @mentions in user input and expand to real content.

Supported providers:
    @file:path/to/file          — read file contents
    @file:path/to/file:10-20    — read specific line range
    @diff                       — git diff (unstaged changes)
    @diff:HEAD~1                — git diff against a ref
    @codebase:query             — ripgrep search across workspace

Usage: Register `expand_context_mentions` as a `user_prompt_submit` lifecycle hook.
The hook parses @mentions, fetches content, and appends it to the prompt.
"""

import logging
import os
import re
import subprocess
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Patterns for @mentions
_FILE_PATTERN = re.compile(r"@file:([^\s]+)")
_DIFF_PATTERN = re.compile(r"@diff(?::([^\s]+))?")
_CODEBASE_PATTERN = re.compile(r"@codebase:([^\s]+)")


def _read_file(path: str, workspace: str) -> str:
    """Read a file, optionally with line range (path:start-end)."""
    line_range = None
    if ":" in path and not os.path.exists(os.path.join(workspace, path)):
        # Check for line range suffix like "file.py:10-20"
        parts = path.rsplit(":", 1)
        range_match = re.match(r"(\d+)-(\d+)", parts[1])
        if range_match:
            path = parts[0]
            line_range = (int(range_match.group(1)), int(range_match.group(2)))

    # Resolve path relative to workspace
    if not os.path.isabs(path):
        full_path = os.path.join(workspace, path)
    else:
        full_path = path

    full_path = os.path.realpath(full_path)
    ws_real = os.path.realpath(workspace)
    # Enforce workspace boundary (covers both absolute paths and traversal)
    if not (full_path.startswith(ws_real + os.sep) or full_path == ws_real):
        return f"[Access denied: path is outside workspace]"

    if not os.path.isfile(full_path):
        return f"[File not found: {path}]"

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"[Error reading {path}: {e}]"

    if line_range:
        start, end = line_range
        start = max(1, start) - 1  # 1-indexed to 0-indexed
        end = min(len(lines), end)
        selected = lines[start:end]
        header = f"# {path} (lines {start+1}-{end})"
    else:
        # Cap at 500 lines
        if len(lines) > 500:
            selected = lines[:500]
            header = f"# {path} (first 500 of {len(lines)} lines)"
        else:
            selected = lines
            header = f"# {path}"

    return f"{header}\n```\n{''.join(selected)}```"


def _git_diff(ref: Optional[str], workspace: str) -> str:
    """Run git diff and return output."""
    # Validate ref: allow standard git ref chars; block path traversal (../) and
    # any chars that could be misused even if passed as a subprocess argument.
    if ref and (
        not re.fullmatch(r'[a-zA-Z0-9._~^@{}\-/]+', ref)
        or '..' in ref  # block path traversal sequences
    ):
        return f"[Invalid git ref: {ref!r}]"
    cmd = ["git", "diff"]
    if ref:
        cmd.append(ref)
    cmd.append("--stat")

    try:
        # First get the stat summary
        stat_result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=workspace, timeout=10,
        )

        # Then get the full diff (capped)
        diff_cmd = ["git", "diff"]
        if ref:
            diff_cmd.append(ref)
        diff_result = subprocess.run(
            diff_cmd, capture_output=True, text=True, cwd=workspace, timeout=10,
        )

        if diff_result.returncode != 0:
            return f"[git diff error: {diff_result.stderr.strip()}]"

        diff_text = diff_result.stdout
        if not diff_text.strip():
            label = f"git diff {ref}" if ref else "git diff"
            return f"[{label}: no changes]"

        # Cap at 200 lines
        lines = diff_text.splitlines()
        if len(lines) > 200:
            diff_text = "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"

        header = f"# git diff {ref}" if ref else "# git diff (unstaged)"
        return f"{header}\n```diff\n{diff_text}\n```"

    except subprocess.TimeoutExpired:
        return "[git diff timed out]"
    except FileNotFoundError:
        return "[git not found]"


def _codebase_search(query: str, workspace: str) -> str:
    """Search codebase using ripgrep."""
    rg_path = getattr(config, "RIPGREP_PATH", "rg")
    cmd = [
        rg_path, "--no-heading", "--line-number", "--color", "never",
        "--max-count", "5",  # max 5 matches per file
        "--max-filesize", "1M",
        "-g", "!.git",
        "-g", "!node_modules",
        "-g", "!__pycache__",
        query,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=workspace, timeout=10,
        )
        output = result.stdout.strip()
        if not output:
            return f"[codebase search '{query}': no results]"

        lines = output.splitlines()
        if len(lines) > 50:
            output = "\n".join(lines[:50]) + f"\n... ({len(lines) - 50} more results)"

        return f"# Codebase search: {query}\n```\n{output}\n```"

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return f"[codebase search '{query}': search tool unavailable]"


def expand_context_mentions(prompt: str, workspace: str = "") -> str:
    """Parse @mentions in prompt and expand to real content.

    Returns the modified prompt with @mentions replaced/appended.
    """
    workspace = workspace or str(config.WORKSPACE_DIR)
    expansions = []

    # @file:path
    for match in _FILE_PATTERN.finditer(prompt):
        path = match.group(1)
        content = _read_file(path, workspace)
        expansions.append(content)

    # @diff or @diff:ref
    for match in _DIFF_PATTERN.finditer(prompt):
        ref = match.group(1)
        content = _git_diff(ref, workspace)
        expansions.append(content)

    # @codebase:query
    for match in _CODEBASE_PATTERN.finditer(prompt):
        query = match.group(1)
        content = _codebase_search(query, workspace)
        expansions.append(content)

    if not expansions:
        return prompt

    # Strip @mentions from original prompt, append expanded context
    cleaned = _FILE_PATTERN.sub("", prompt)
    cleaned = _DIFF_PATTERN.sub("", cleaned)
    cleaned = _CODEBASE_PATTERN.sub("", cleaned)
    cleaned = cleaned.strip()

    context_block = "\n\n---\n**Attached context:**\n\n" + "\n\n".join(expansions)
    return cleaned + context_block


async def context_provider_hook(prompt: str, agent: str = "") -> Optional[str]:
    """Lifecycle hook handler for user_prompt_submit.

    Registered in graph.py. Expands @mentions before the prompt
    reaches the agent.
    """
    if not any(marker in prompt for marker in ("@file:", "@diff", "@codebase:")):
        return None  # No mentions, pass through
    expanded = expand_context_mentions(prompt)
    if expanded != prompt:
        logger.info("[context] Expanded @mentions in prompt")
        return expanded
    return None
