"""
Tool: code_search — search for patterns across files with context.
Supports ripgrep (fast, native) with fallback to Python regex.
All outputs go through universal truncation.
"""
import os
import re
import subprocess
import shutil
from fnmatch import fnmatch
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path, IGNORE_DIRS, BINARY_EXT
from models.tool_schemas import CodeSearchArgs, GrepSearchArgs, BatchReadArgs

# Check for ripgrep availability
_RG_PATH = shutil.which(getattr(config, "RIPGREP_PATH", "rg"))


def _ripgrep_search(
    query: str,
    search_dir: str,
    file_pattern: str = "*",
    max_results: int = 100,
    context_lines: int = 0,
    case_sensitive: bool = False,
) -> str | None:
    """Try ripgrep-based search. Returns None if rg not available."""
    if not _RG_PATH:
        return None

    cmd = [
        _RG_PATH,
        "--line-number",
        "--no-heading",
        "--color=never",
        f"--max-count={max(10, max_results)}",  # per-file cap, respects caller's limit
        f"--max-columns=2000",
        "--max-columns-preview",
        "--sort=modified",  # most recently modified first (like OpenCode)
    ]

    if not case_sensitive:
        cmd.append("--ignore-case")

    if context_lines > 0:
        cmd.append(f"--context={context_lines}")

    if file_pattern and file_pattern != "*":
        cmd.extend(["--glob", file_pattern])

    cmd.extend([query, search_dir])

    # Two attempts: normal timeout, then 2x timeout on first failure
    for _attempt in range(2):
        timeout = config.TOOL_TIMEOUT * (1 + _attempt)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            break
        except subprocess.TimeoutExpired:
            if _attempt == 0:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"[code_search] ripgrep timed out after {timeout}s — retrying with {timeout * 2}s"
                )
                continue
            return None  # Both attempts timed out → fall back to Python
        except (FileNotFoundError, OSError):
            return None

    if result.returncode > 1:  # 0 = matches, 1 = no matches, 2+ = error
        return None

    output = result.stdout
    if not output.strip():
        return f"No matches found for '{query}' in {search_dir}"

    # Make paths relative
    lines = output.split("\n")
    formatted = []
    match_count = 0
    for line in lines:
        if line.strip():
            # Replace absolute path with relative
            if line.startswith(search_dir):
                line = line[len(search_dir):].lstrip(os.sep)
            formatted.append(line)
            if not line.startswith("--"):  # separator lines
                match_count += 1
        else:
            formatted.append(line)

        if match_count >= max_results:
            formatted.append(f"\n... (showing first {max_results} matches)")
            break

    header = f"🔍 Search results for '{query}' (ripgrep):\n\n"
    return header + "\n".join(formatted)


@tool(args_schema=CodeSearchArgs)
def code_search(
    query: str,
    directory: str = "",
    file_pattern: str = "*",
    max_results: int = 30,
    context_lines: int = 0,
    case_sensitive: bool = False,
) -> str:
    """Search for a text pattern or keyword across files in a directory.

    Uses ripgrep for fast native search when available, with Python regex fallback.
    Results are sorted by modification time (most recent first).

    Args:
        query: The search term, keyword, or regex pattern to find.
        directory: Directory to search in. Defaults to workspace root.
        file_pattern: Glob pattern to filter files (e.g. '*.py', '*.c', '*.m'). Use '*' for all files.
        max_results: Maximum number of matching lines to return. Default 30.
        context_lines: Number of context lines to show before/after each match. Default 0.
        case_sensitive: Whether search is case-sensitive. Default False (case-insensitive).

    Returns:
        Formatted search results with file paths, line numbers, and matching lines.
    """
    search_dir = directory or config.WORKSPACE_DIR
    if not os.path.isdir(search_dir):
        return f"Error: Directory '{search_dir}' does not exist."

    # Try ripgrep first
    rg_result = _ripgrep_search(
        query, search_dir, file_pattern, max_results, context_lines, case_sensitive
    )
    if rg_result is not None:
        return truncate_output(rg_result)

    # Fallback: Python regex search
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query, flags)
    except re.error:
        pattern = re.compile(re.escape(query), flags)

    results = []
    files_searched = 0
    files_matched = 0

    # Collect files sorted by modification time (most recent first)
    all_files = []
    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in BINARY_EXT:
                continue
            if file_pattern != "*" and not fnmatch(fname, file_pattern):
                continue
            fpath = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                mtime = 0
            all_files.append((fpath, mtime))

    # Sort by modification time, newest first
    all_files.sort(key=lambda x: x[1], reverse=True)

    for fpath, _ in all_files:
        rel_path = os.path.relpath(fpath, search_dir)
        files_searched += 1

        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                file_lines = f.readlines()
        except (PermissionError, IsADirectoryError, OSError):
            continue

        file_matches = []
        for line_num, line in enumerate(file_lines):
            if pattern.search(line):
                file_matches.append(line_num)

        if not file_matches:
            continue

        files_matched += 1

        for match_idx in file_matches:
            if len(results) >= max_results:
                break

            start = max(0, match_idx - context_lines)
            end = min(len(file_lines), match_idx + context_lines + 1)

            if context_lines > 0:
                block = []
                for i in range(start, end):
                    prefix = ">> " if i == match_idx else "   "
                    block.append(f"  {prefix}{i + 1:4d} | {file_lines[i].rstrip()}")
                results.append(f"📄 {rel_path}:{match_idx + 1}")
                results.extend(block)
                results.append("")
            else:
                results.append(f"{rel_path}:{match_idx + 1}: {file_lines[match_idx].rstrip()}")

        if len(results) >= max_results:
            break

    if not results:
        return f"No matches found for '{query}' in {search_dir} ({files_searched} files searched)"

    header = f"🔍 Found matches in {files_matched} file(s) ({files_searched} searched):\n\n"
    return truncate_output(header + "\n".join(results))


@tool(args_schema=GrepSearchArgs)
def grep_search(
    keyword: str,
    file_path: str,
    context_lines: int = 2,
) -> str:
    """Search for a keyword within a single file and show surrounding context.

    Use this to find specific functions, variables, or patterns in a known file.

    Args:
        keyword: The keyword or pattern to search for.
        file_path: Path to the file to search in (relative to workspace or absolute).
        context_lines: Number of context lines above and below each match.

    Returns:
        All matches with line numbers and surrounding context.
    """
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        return f"Error: File '{file_path}' not found."

    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except PermissionError:
        return f"Error: Permission denied reading '{file_path}'."

    try:
        pattern = re.compile(keyword, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

    matches = [i for i, line in enumerate(lines) if pattern.search(line)]

    if not matches:
        return f"No matches for '{keyword}' in {file_path} ({len(lines)} lines)"

    results = [f"🔍 {len(matches)} matches for '{keyword}' in {file_path}:\n"]

    for match_idx in matches[:50]:
        start = max(0, match_idx - context_lines)
        end = min(len(lines), match_idx + context_lines + 1)

        results.append(f"── Line {match_idx + 1} ──")
        for i in range(start, end):
            prefix = ">> " if i == match_idx else "   "
            results.append(f"{prefix}{i + 1:4d} | {lines[i].rstrip()}")
        results.append("")

    return truncate_output("\n".join(results))


@tool(args_schema=BatchReadArgs)
def batch_read(
    file_paths: list[str],
) -> str:
    """Read multiple files at once and return their contents.

    Use this to quickly read several related files in one call.
    Outputs are truncated per-file to prevent context overflow.

    Args:
        file_paths: List of file paths to read (relative to workspace or absolute).

    Returns:
        Combined contents of all files, each with header and line numbers.
    """
    if not file_paths:
        return "Error: No file paths provided."

    _BATCH_LIMIT = getattr(config, "BATCH_READ_LIMIT", 10)
    if len(file_paths) > _BATCH_LIMIT:
        return f"Error: Maximum {_BATCH_LIMIT} files per batch. Please split into smaller batches."

    # Import device path block list from file_ops for consistent protection
    from agent.tools.file_ops import _BLOCKED_DEVICE_PATHS

    results = []

    for fp in file_paths:
        # Block device paths that cause infinite reads / hangs
        fp_norm = fp.replace("\\", "/").rstrip("/")
        if fp_norm in _BLOCKED_DEVICE_PATHS:
            results.append(f"❌ {fp} — Device path not allowed\n")
            continue

        resolved = _resolve_path(fp)
        resolved_norm = resolved.replace("\\", "/")
        if any(resolved_norm == d or resolved_norm.startswith(d + "/") for d in _BLOCKED_DEVICE_PATHS):
            results.append(f"❌ {fp} — Device path not allowed\n")
            continue
        if resolved_norm.startswith("/proc/") and (
            resolved_norm.endswith("/fd/0")
            or resolved_norm.endswith("/fd/1")
            or resolved_norm.endswith("/fd/2")
        ):
            results.append(f"❌ {fp} — stdio alias not allowed\n")
            continue

        if not os.path.isfile(resolved):
            results.append(f"❌ {fp} — File not found\n")
            continue

        try:
            with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except PermissionError:
            results.append(f"❌ {fp} — Permission denied\n")
            continue

        lines = content.split("\n")
        total = len(lines)

        # Truncate very large files
        if total > 500:
            numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(1, 501)]
            numbered.append(f"\n... truncated ({total} lines total, showing first 500)")
        else:
            numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(1, total + 1)]

        results.append(f"{'═' * 60}")
        results.append(f"📄 {fp} ({total} lines)")
        results.append(f"{'═' * 60}")
        results.extend(numbered)
        results.append("")

    return truncate_output("\n".join(results))


def _resolve_path(p: str) -> str:
    return resolve_tool_path(p)
