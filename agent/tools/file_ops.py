"""
Tools: file operations — read, write, edit (with fuzzy fallback), list, glob.

Edit upgraded with multi-strategy matching inspired by OpenCode:
1. Exact match (original)
2. Line-trimmed match (strips whitespace)
3. Levenshtein distance fuzzy match
4. Block anchor match (first + last lines)

All outputs go through truncation layer.
"""
import os
import glob
import difflib
import pathlib
from pathlib import Path
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path, resolve_path_safe
from models.tool_schemas import (
    FileReadArgs, FileWriteArgs, FileEditArgs, FileListArgs, GlobSearchArgs,
    FileEditBatchArgs, ApplyPatchInput,
)


# ── Diff syntax highlighting ─────────────────────────────────

def _render_diff(diff_text: str, file_path: str) -> str:
    """Render unified diff with syntax highlighting via Rich.

    Returns an ANSI-coloured string suitable for terminal output.
    Falls back to the raw diff text if Rich is unavailable or
    the terminal does not support colour.
    """
    try:
        from rich.syntax import Syntax
        from rich.console import Console

        ext = Path(file_path).suffix.lstrip(".")
        lang_map = {
            "py": "python", "ts": "typescript", "js": "javascript",
            "rs": "rust", "go": "go", "java": "java", "cpp": "cpp",
            "c": "c", "md": "markdown", "json": "json", "yaml": "yaml",
            "yml": "yaml", "toml": "toml", "sh": "bash", "html": "html",
            "css": "css",
        }
        lang = lang_map.get(ext, "diff")

        _console = Console(force_terminal=True, width=120)
        with _console.capture() as capture:
            _console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False))
        return capture.get()
    except Exception:
        return diff_text

# Pending diffs for frontend diff view (file_path → {original, modified})
# server/main.py reads and clears this after emitting file:diff events
PENDING_DIFFS: dict[str, dict[str, str]] = {}


# Device paths that cause infinite reads / hangs — block them (CC: FileReadTool)
_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom",
    "/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr",
})

# Bare git repo defense: block writes to .git internals that can enable RCE
# via git hooks (pre-commit, post-checkout) or core.fsmonitor in git config.
_GIT_BLOCKED = {"HEAD", "config", "COMMIT_EDITMSG", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD"}
_GIT_BLOCKED_DIRS = {"objects", "refs", "hooks", "logs"}

_GIT_WRITE_BLOCKED_MSG = (
    "⛔ Write blocked: modifying .git internals can enable RCE via git hooks or "
    "core.fsmonitor. Use git commands instead."
)


def _is_git_internal_write(resolved_path: str) -> bool:
    """Return True if path targets a .git internal that could enable RCE via git hooks/fsmonitor."""
    parts = pathlib.Path(resolved_path).parts
    for i, part in enumerate(parts):
        if part == ".git":
            rest = parts[i + 1:]
            if not rest:
                return False  # writing to .git dir itself is fine
            if rest[0] in _GIT_BLOCKED_DIRS:
                return True
            if rest[0] in _GIT_BLOCKED and len(rest) == 1:
                return True
    return False



@tool(args_schema=FileReadArgs)
def file_read(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read the contents of a file.

    Args:
        file_path: Absolute or relative path to the file. Relative paths resolve from workspace.
        start_line: Optional start line (1-indexed). 0 means read from beginning.
        end_line: Optional end line (1-indexed, inclusive). 0 means read to end.

    Returns:
        File contents with line numbers, or error message.
    """
    # Block infinite-read device paths (CC: prevents hangs on /dev/zero etc.)
    normalized = file_path.replace("\\", "/").rstrip("/")
    if normalized in _BLOCKED_DEVICE_PATHS:
        return f"Error: Reading '{file_path}' is not allowed (device path)."

    resolved = _resolve_path(file_path)

    # Block again after path resolution
    resolved_norm = resolved.replace("\\", "/")
    if any(resolved_norm == d or resolved_norm.startswith(d + "/") for d in _BLOCKED_DEVICE_PATHS):
        return f"Error: Reading '{file_path}' is not allowed (device path)."

    if not os.path.isfile(resolved):
        return f"Error: File '{file_path}' not found."

    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except PermissionError:
        return f"Error: Permission denied reading '{file_path}'."

    total = len(lines)
    LIMIT = 2000

    if start_line == 0 and end_line == 0 and total > LIMIT:
        end_line = LIMIT
        warning = f"\n⚠️ File is large ({total} lines). Showing first {LIMIT} lines. Use start_line/end_line to read more."
    else:
        warning = ""

    s = max(0, start_line - 1) if start_line > 0 else 0
    e = min(total, end_line) if end_line > 0 else total
    selected = lines[s:e]

    numbered = []
    for i, line in enumerate(selected, start=s + 1):
        numbered.append(f"{i:4d} | {line.rstrip()}")

    header = f"📄 {resolved} ({total} lines total"
    if start_line or end_line:
        header += f", showing L{s+1}-{e}"
    header += f"){warning}\n"

    return truncate_output(header + "\n".join(numbered))


@tool(args_schema=FileWriteArgs)
def file_write(file_path: str, content: str, create_dirs: bool = True) -> str:
    """Write content to a file. Creates the file if it doesn't exist.
    WARNING: This overwrites the entire file. Use file_edit for partial updates.

    Args:
        file_path: Path to the file. Relative paths resolve from workspace.
        content: Content to write to the file.
        create_dirs: If True, create parent directories if they don't exist.

    Returns:
        Success or error message.
    """
    resolved = _resolve_path(file_path)

    # Bare git repo defense
    if _is_git_internal_write(resolved):
        return _GIT_WRITE_BLOCKED_MSG

    try:
        if create_dirs:
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)

        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)

        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"✅ Written {lines} lines to {resolved}"
    except PermissionError:
        return f"Error: Permission denied writing to '{file_path}'."
    except Exception as e:
        return f"Error writing file: {e}"


# ─────────────────────────────────────────────────────────────
# Fuzzy edit strategies (inspired by OpenCode's edit.ts & Aider)
# ─────────────────────────────────────────────────────────────

def _adjust_indentation(old_string: str, matched_text: str, new_string: str) -> str:
    """Adjust the indentation of new_string to match how matched_text differs from old_string."""
    old_lines = old_string.split('\n')
    matched_lines = matched_text.split('\n')
    new_lines = new_string.split('\n')
    
    if not old_lines or not matched_lines or not new_lines:
        return new_string
        
    old_indent = len(old_lines[0]) - len(old_lines[0].lstrip())
    match_indent = len(matched_lines[0]) - len(matched_lines[0].lstrip())
    
    indent_diff = match_indent - old_indent
    
    if indent_diff == 0:
        return new_string
        
    adjusted_lines = []
    for line in new_lines:
        if not line.strip():
            adjusted_lines.append(line)
            continue
            
        current_indent = len(line) - len(line.lstrip())
        if indent_diff > 0:
            adjusted_lines.append(" " * indent_diff + line)
        else:
            # indent_diff < 0, subtract indentation if possible
            remove_spaces = min(current_indent, -indent_diff)
            adjusted_lines.append(line[remove_spaces:])
            
    return '\n'.join(adjusted_lines)


def _exact_match(content: str, old_string: str) -> tuple[int, str | None]:
    """Strategy 1: Exact match (original behavior)."""
    count = content.count(old_string)
    if count == 1:
        return 1, content.replace(old_string, "", 1)  # placeholder — actual replace in caller
    return count, None


def _line_trimmed_match(content: str, old_string: str) -> tuple[str | None, str]:
    """Strategy 2: Match with stripped whitespace per line.
    Handles indentation/whitespace differences."""
    content_lines = content.split("\n")
    old_lines = old_string.strip().split("\n")
    old_stripped = [line.strip() for line in old_lines]

    if not old_stripped:
        return None, "empty old_string"

    # Slide window over content lines
    for i in range(len(content_lines) - len(old_stripped) + 1):
        window = content_lines[i:i + len(old_stripped)]
        if [line.strip() for line in window] == old_stripped:
            # Found match — return the original lines segment
            matched_text = "\n".join(content_lines[i:i + len(old_stripped)])
            return matched_text, "line_trimmed"

    return None, "no match"


def _levenshtein_match(content: str, old_string: str, threshold: float = 0.85) -> tuple[str | None, str]:
    """Strategy 3: Fuzzy match using SequenceMatcher (Levenshtein-like).
    Finds the best matching block with similarity >= threshold."""
    content_lines = content.split("\n")
    old_lines = old_string.strip().split("\n")
    n = len(old_lines)

    if n == 0:
        return None, "empty"

    best_ratio = 0.0
    best_start = -1

    # Slide window
    for i in range(len(content_lines) - n + 1):
        window = "\n".join(content_lines[i:i + n])
        ratio = difflib.SequenceMatcher(None, old_string.strip(), window.strip()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if best_ratio >= threshold and best_start >= 0:
        matched_text = "\n".join(content_lines[best_start:best_start + n])
        return matched_text, f"levenshtein (similarity={best_ratio:.2%})"

    return None, f"best similarity {best_ratio:.2%} < {threshold:.0%}"


def _block_anchor_match(content: str, old_string: str) -> tuple[str | None, str]:
    """Strategy 4: Match using first and last non-empty lines as anchors.
    Good when middle content changed but boundaries are recognizable."""
    old_lines = old_string.strip().split("\n")
    old_nonempty = [l for l in old_lines if l.strip()]
    if len(old_nonempty) < 2:
        return None, "need >= 2 non-empty lines for anchor"

    first_anchor = old_nonempty[0].strip()
    last_anchor = old_nonempty[-1].strip()
    content_lines = content.split("\n")

    # Find first anchor
    start_candidates = [i for i, l in enumerate(content_lines) if l.strip() == first_anchor]
    if not start_candidates:
        return None, "first anchor not found"

    # For each start candidate, find matching last anchor
    for start in start_candidates:
        max_end = min(start + len(old_lines) + 5, len(content_lines))  # allow slight size difference
        for end in range(start + len(old_nonempty) - 1, max_end):
            if end < len(content_lines) and content_lines[end].strip() == last_anchor:
                matched_text = "\n".join(content_lines[start:end + 1])
                return matched_text, "block_anchor"

    return None, "last anchor not found near first"


@tool(args_schema=FileEditArgs)
def file_edit(file_path: str, old_string: str, new_string: str) -> str:
    """Edit a file by replacing a string with a new one.

    Uses multi-strategy matching:
    1. Exact match (fastest, most precise)
    2. Line-trimmed match (handles whitespace/indentation differences)
    3. Levenshtein fuzzy match (handles minor typos, 85% similarity threshold)
    4. Block anchor match (uses first/last lines as anchors)

    The `old_string` should be unique in the file.

    Args:
        file_path: Path to the file.
        old_string: The string to find and replace (can have minor whitespace differences).
        new_string: The new string to replace it with.

    Returns:
        Success message with strategy used, or error.
    """
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        return f"Error: File '{file_path}' not found."

    # Bare git repo defense
    if _is_git_internal_write(resolved):
        return _GIT_WRITE_BLOCKED_MSG

    try:
        # newline="" preserves raw line endings (no OS-level \r\n→\n conversion)
        # so we can accurately detect and later restore the original line ending style.
        with open(resolved, "r", encoding="utf-8", errors="ignore", newline="") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    # ── Line-ending awareness ────────────────────────────────
    # Detect dominant line ending; normalise to LF for matching; restore on write.
    original_le = "\r\n" if "\r\n" in content else "\n"
    content_lf = content.replace("\r\n", "\n")
    old_string_lf = old_string.replace("\r\n", "\n")
    new_string_lf = new_string.replace("\r\n", "\n")

    # Strategy 1: Exact match (on LF-normalised content)
    count = content_lf.count(old_string_lf)
    if count == 1:
        result_lf = content_lf.replace(old_string_lf, new_string_lf, 1)
        new_content = result_lf.replace("\n", original_le)
        return _write_edit(resolved, file_path, content, new_content, "exact_match")
    elif count > 1:
        return f"❌ Error: 'old_string' found {count} times. Please provide more specific context."

    # Strategy 2: Line-trimmed match
    matched_text, info = _line_trimmed_match(content_lf, old_string_lf)
    if matched_text is not None:
        if content_lf.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string_lf, matched_text, new_string_lf)
            result_lf = content_lf.replace(matched_text, adjusted_new, 1)
            new_content = result_lf.replace("\n", original_le)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # Strategy 3: Levenshtein fuzzy match
    matched_text, info = _levenshtein_match(content_lf, old_string_lf)
    if matched_text is not None:
        if content_lf.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string_lf, matched_text, new_string_lf)
            result_lf = content_lf.replace(matched_text, adjusted_new, 1)
            new_content = result_lf.replace("\n", original_le)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # Strategy 4: Block anchor match
    matched_text, info = _block_anchor_match(content_lf, old_string_lf)
    if matched_text is not None:
        if content_lf.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string_lf, matched_text, new_string_lf)
            result_lf = content_lf.replace(matched_text, adjusted_new, 1)
            new_content = result_lf.replace("\n", original_le)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # All strategies failed — provide helpful error with closest match
    return _edit_failed_hint(content_lf, old_string_lf, file_path)


def _write_edit(resolved: str, file_path: str, old_content: str, new_content: str, strategy: str) -> str:
    """Write the edited content and return result with diff preview."""
    try:
        # Use newline="" to suppress OS-level \n→\r\n translation on Windows;
        # line endings are already restored to original (CRLF or LF) before this call.
        with open(resolved, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
    except Exception as e:
        return f"Error writing file: {e}"

    # Store diff for frontend diff view
    PENDING_DIFFS[resolved] = {
        "original": old_content,
        "modified": new_content,
    }

    # Generate compact diff for confirmation
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    diff_preview = ""
    if diff:
        diff_lines = diff[:30]  # limit diff output
        raw_diff = "\n".join(diff_lines)
        if len(diff) > 30:
            raw_diff += f"\n... ({len(diff) - 30} more diff lines)"
        highlighted = _render_diff(raw_diff, file_path)
        diff_preview = f"\n\nDiff:\n{highlighted}"

    return f"✅ Edited {file_path} (strategy: {strategy}){diff_preview}"


def _edit_failed_hint(content: str, old_string: str, file_path: str) -> str:
    """When all edit strategies fail, show the closest matching block."""
    old_lines = old_string.strip().split("\n")
    content_lines = content.split("\n")
    n = len(old_lines)

    best_ratio = 0.0
    best_start = -1

    for i in range(max(1, len(content_lines) - n + 1)):
        window = "\n".join(content_lines[i:i + n])
        ratio = difflib.SequenceMatcher(None, old_string.strip(), window.strip()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    hint = f"❌ Error: Could not find matching text in {file_path}.\n"
    hint += f"All 4 strategies failed (exact, line-trimmed, levenshtein, block-anchor).\n"

    if best_ratio > 0.5 and best_start >= 0:
        closest = "\n".join(content_lines[best_start:best_start + n])
        hint += f"\nClosest match (similarity: {best_ratio:.0%}) at line {best_start + 1}:\n"
        hint += f"```\n{closest[:500]}\n```\n"
        hint += "\nTip: Use file_read to see the exact current content, then retry with the correct text."
    else:
        hint += "\nTip: The text might not exist in this file. Use grep_search or file_read to verify."

    return hint


@tool(args_schema=FileEditBatchArgs)
def file_edit_batch(edits: list) -> str:
    """Apply multiple file edits atomically — all succeed or none are written.

    Validates all edits in memory first. If any edit cannot be matched, the
    entire batch is aborted with no files changed. This ensures consistency
    across multi-file refactors.

    Each edit uses the same 4-strategy matching as `file_edit` (exact, trimmed,
    fuzzy, anchor).

    Args:
        edits: List of {file_path, old_string, new_string} edit operations.

    Returns:
        Summary of all edits applied, or an error showing which edit failed.
    """
    if not edits:
        return "❌ Error: No edits provided."

    # Normalize — edits may arrive as dicts or SingleEditItem objects
    normalized = []
    for item in edits:
        if hasattr(item, "file_path"):
            normalized.append((item.file_path, item.old_string, item.new_string))
        elif isinstance(item, dict):
            normalized.append((item["file_path"], item["old_string"], item["new_string"]))
        else:
            return f"❌ Error: Invalid edit item: {item!r}"

    # Phase 1: validate + compute new contents (in memory only)
    pending: list[tuple[str, str, str, str]] = []  # (resolved, file_path, old_content, new_content)

    for idx, (file_path, old_string, new_string) in enumerate(normalized):
        resolved = _resolve_path(file_path)
        if not os.path.isfile(resolved):
            return f"❌ Batch aborted at edit #{idx + 1}: File '{file_path}' not found. No files changed."

        # Bare git repo defense
        if _is_git_internal_write(resolved):
            return f"❌ Batch aborted at edit #{idx + 1}: {_GIT_WRITE_BLOCKED_MSG}"

        try:
            with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            return f"❌ Batch aborted at edit #{idx + 1}: Cannot read '{file_path}': {e}. No files changed."

        # Apply matching strategies (same as file_edit)
        new_content = _apply_edit(content, old_string, new_string)
        if new_content is None:
            # Provide helpful hint
            hint = _edit_failed_hint(content, old_string, file_path)
            return (
                f"❌ Batch aborted at edit #{idx + 1} ({file_path}):\n"
                f"{hint}\nNo files changed."
            )

        pending.append((resolved, file_path, content, new_content))

    # Phase 2: write all with rollback on failure
    results = []
    written: list[tuple[str, str]] = []  # (resolved_path, original_content)
    for resolved, file_path, old_content, new_content in pending:
        try:
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)
            written.append((resolved, old_content))

            PENDING_DIFFS[resolved] = {"original": old_content, "modified": new_content}

            diff = list(difflib.unified_diff(
                old_content.split("\n"), new_content.split("\n"), lineterm="", n=1
            ))
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            results.append(f"  ✅ {file_path} (+{added}/-{removed} lines)")
        except Exception as e:
            # Roll back all already-written files
            for r_path, original in written:
                try:
                    with open(r_path, "w", encoding="utf-8") as f:
                        f.write(original)
                except Exception:
                    pass  # best-effort rollback
            return f"❌ Write failed on {file_path}: {e}. Rolled back {len(written)} file(s)."

    return f"✅ Batch edit applied {len(results)}/{len(normalized)} files:\n" + "\n".join(results)


def _apply_edit(content: str, old_string: str, new_string: str) -> str | None:
    """Try all matching strategies. Returns new content or None if all fail."""
    # Strategy 1: exact
    count = content.count(old_string)
    if count == 1:
        return content.replace(old_string, new_string, 1)
    if count > 1:
        return None  # ambiguous

    # Strategy 2: line-trimmed
    matched_text, _ = _line_trimmed_match(content, old_string)
    if matched_text is not None and content.count(matched_text) == 1:
        adjusted = _adjust_indentation(old_string, matched_text, new_string)
        return content.replace(matched_text, adjusted, 1)

    # Strategy 3: levenshtein
    matched_text, _ = _levenshtein_match(content, old_string)
    if matched_text is not None and content.count(matched_text) == 1:
        adjusted = _adjust_indentation(old_string, matched_text, new_string)
        return content.replace(matched_text, adjusted, 1)

    # Strategy 4: anchor
    matched_text, _ = _block_anchor_match(content, old_string)
    if matched_text is not None and content.count(matched_text) == 1:
        adjusted = _adjust_indentation(old_string, matched_text, new_string)
        return content.replace(matched_text, adjusted, 1)

    return None


@tool(args_schema=FileListArgs)
def file_list(directory: str = "", max_depth: int = 3, show_size: bool = False) -> str:
    """List files and directories in a tree-like format.

    Args:
        directory: Directory to list. Defaults to workspace root.
        max_depth: Maximum depth to recurse. Default 3.
        show_size: Whether to show file sizes.

    Returns:
        Tree-like directory listing.
    """
    if directory:
        target = resolve_tool_path(directory)
    else:
        target = config.WORKSPACE_DIR
    if not os.path.isdir(target):
        return f"Error: Directory '{target}' does not exist."

    lines = [f"📂 {target}/"]
    _tree(target, "", 0, max_depth, show_size, lines)

    if len(lines) == 1:
        lines.append("  (empty)")

    return truncate_output("\n".join(lines))


@tool(args_schema=GlobSearchArgs)
def glob_search(pattern: str, directory: str = "") -> str:
    """Find files using glob patterns (e.g. '**/*.py').

    Args:
        pattern: Glob pattern. '**' matches directories recursively.
        directory: Root directory to start search. Defaults to workspace.

    Returns:
        List of matching files.
    """
    # Block Windows UNC paths that can trigger NTLM credential leaks (CC: GlobTool)
    dir_norm = (directory or "").replace("\\", "/")
    if dir_norm.startswith("//") or dir_norm.startswith("\\\\"):
        return "Error: UNC paths are not allowed."

    # Sandbox directory parameter to workspace boundary
    if directory:
        target = resolve_tool_path(directory)
    else:
        target = config.WORKSPACE_DIR
    if not os.path.isdir(target):
        return f"Error: Directory '{target}' does not exist."

    # Block UNC after resolution too
    target_norm = target.replace("\\", "/")
    if target_norm.startswith("//") or target_norm.startswith("\\\\"):
        return "Error: UNC paths are not allowed."

    search_pattern = os.path.join(target, pattern)

    try:
        # recursive=True needed for ** usage
        matches = glob.glob(search_pattern, recursive=True)
    except Exception as e:
        return f"Error during glob search: {e}"

    # Filter out ignored dirs
    filtered = []
    ignore = {
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        "env", "dist", "build", ".next", ".cache",
    }

    for m in matches:
        if os.path.isdir(m):
            continue

        # Check if any part of path is in ignore list
        rel = os.path.relpath(m, target)
        parts = rel.split(os.sep)
        if any(p in ignore for p in parts):
            continue

        filtered.append(rel)

    if not filtered:
        return f"No files found for pattern '{pattern}' in {target}"

    # Cap at 100 results — mark truncated (CC: GlobTool MAX_RESULTS = 100)
    _GLOB_MAX = 100
    truncated = len(filtered) > _GLOB_MAX
    if truncated:
        filtered = filtered[:_GLOB_MAX]
        
    result = f"Found {len(filtered)} file(s) for '{pattern}'"
    if truncated:
        result += f" (showing first {_GLOB_MAX} — use a more specific pattern for full results)"
    result += ":\n" + "\n".join(sorted(filtered))
    return truncate_output(result)


def _tree(path: str, prefix: str, depth: int, max_depth: int, show_size: bool, lines: list):
    """Recursive tree builder."""
    if depth >= max_depth:
        return

    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        lines.append(f"{prefix}  ⚠️ Permission denied")
        return

    # Filter ignored dirs
    ignore = {
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        "env", "dist", "build", ".next", ".cache",
    }
    entries = [e for e in entries if e not in ignore]

    for i, entry in enumerate(entries):
        full = os.path.join(path, entry)
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        if os.path.isdir(full):
            child_count = 0
            try:
                child_count = len(os.listdir(full))
            except PermissionError:
                pass
            lines.append(f"{prefix}{connector}📁 {entry}/ ({child_count} items)")
            _tree(full, prefix + extension, depth + 1, max_depth, show_size, lines)
        else:
            size_str = ""
            if show_size:
                try:
                    size = os.path.getsize(full)
                    size_str = f" ({_fmt_size(size)})"
                except OSError:
                    pass
            lines.append(f"{prefix}{connector}{entry}{size_str}")


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f}{unit}" if unit == "B" else f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


def _resolve_path(p: str) -> str:
    return resolve_tool_path(p)


# ─────────────────────────────────────────────────────────────
# apply_patch — unified diff application
# ─────────────────────────────────────────────────────────────

def _parse_unified_diff(patch: str) -> list[dict]:
    """Parse a unified diff string into a list of file-level records.

    Each record has keys:
        old_path  – path from '--- ' line (None for new-file creation)
        new_path  – path from '+++ ' line (None for file deletion)
        hunks     – list of hunk dicts: {old_start, old_count, new_start, new_count, lines}
    """
    records: list[dict] = []
    current: dict | None = None
    current_hunk: dict | None = None

    def _strip_ab(path: str) -> str:
        """Strip leading 'a/' or 'b/' prefix that git diff adds."""
        if path.startswith("a/") or path.startswith("b/"):
            return path[2:]
        return path

    for raw_line in patch.splitlines():
        if raw_line.startswith("--- "):
            # Start of a new file block — save previous
            if current is not None:
                if current_hunk is not None:
                    current["hunks"].append(current_hunk)
                    current_hunk = None
                records.append(current)
            path = raw_line[4:].split("\t")[0].strip()
            current = {
                "old_path": None if path == "/dev/null" else _strip_ab(path),
                "new_path": None,
                "hunks": [],
            }
            current_hunk = None

        elif raw_line.startswith("+++ ") and current is not None:
            path = raw_line[4:].split("\t")[0].strip()
            current["new_path"] = None if path == "/dev/null" else _strip_ab(path)

        elif raw_line.startswith("@@ ") and current is not None:
            # Flush previous hunk
            if current_hunk is not None:
                current["hunks"].append(current_hunk)
            # Parse @@ -old_start[,old_count] +new_start[,new_count] @@
            import re as _re
            m = _re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw_line)
            if m:
                current_hunk = {
                    "old_start": int(m.group(1)),
                    "old_count": int(m.group(2)) if m.group(2) is not None else 1,
                    "new_start": int(m.group(3)),
                    "new_count": int(m.group(4)) if m.group(4) is not None else 1,
                    "lines": [],
                }
            else:
                current_hunk = None

        elif current_hunk is not None:
            # Hunk body lines: ' ' context, '+' addition, '-' deletion
            if raw_line.startswith(("+", "-", " ")):
                current_hunk["lines"].append(raw_line)
            # Lines starting with '\' (e.g. "\ No newline at end of file") are skipped

    # Flush last hunk / record
    if current is not None:
        if current_hunk is not None:
            current["hunks"].append(current_hunk)
        records.append(current)

    return records


def _apply_hunks(original_lines: list[str], hunks: list[dict]) -> list[str]:
    """Apply a list of parsed hunks to original_lines (0-indexed list, no newlines).

    Returns the new list of lines.
    """
    result: list[str] = []
    old_pos = 0  # 1-indexed position in original (matches hunk old_start)

    for hunk in hunks:
        old_start = hunk["old_start"]
        # Copy unchanged lines before this hunk (old_pos..old_start-1)
        while old_pos < old_start - 1:
            if old_pos < len(original_lines):
                result.append(original_lines[old_pos])
            old_pos += 1

        # Apply hunk lines
        for hline in hunk["lines"]:
            tag = hline[0] if hline else " "
            text = hline[1:] if hline else ""
            if tag == " ":
                # Context line — copy from original
                if old_pos < len(original_lines):
                    result.append(original_lines[old_pos])
                old_pos += 1
            elif tag == "-":
                # Deletion — skip the original line
                old_pos += 1
            elif tag == "+":
                # Addition — insert new line
                result.append(text)
            # Other characters (e.g. '\\') are ignored

    # Copy any remaining original lines after the last hunk
    while old_pos < len(original_lines):
        result.append(original_lines[old_pos])
        old_pos += 1

    return result


@tool(args_schema=ApplyPatchInput)
def apply_patch(patch: str, workspace: str = "") -> str:
    """Apply a unified diff patch to one or more files atomically.

    Parses standard unified diff format (as produced by 'git diff' or 'diff -u').
    Supports creating new files (--- /dev/null) and deleting files (+++ /dev/null).

    All file writes are collected first; if any write fails the already-written
    files are rolled back to their original content.

    Args:
        patch: Unified diff text (--- a/file ... +++ b/file ... @@ ... @@).
        workspace: Optional root directory for relative paths. Empty = workspace root.

    Returns:
        Summary of files patched, or an error message.
    """
    ws = workspace.strip() if workspace.strip() else config.WORKSPACE_DIR

    records = _parse_unified_diff(patch)
    if not records:
        return "❌ Error: No valid file blocks found in patch."

    # ── Phase 1: Parse + validate all file operations ────────
    pending: list[tuple[str, str | None, str | None]] = []
    # Each entry: (resolved_path, original_content_or_None, new_content_or_None)
    # new_content = None → delete the file
    # original_content = None → create new file

    for rec in records:
        old_path = rec["old_path"]
        new_path = rec["new_path"]

        # Determine target path (prefer new_path for rename/creation, old_path for deletion)
        target_path = new_path if new_path is not None else old_path
        if target_path is None:
            return "❌ Error: Patch block has neither old nor new path."

        # Security: resolve within workspace
        resolved = resolve_path_safe(target_path, ws)
        if resolved is None:
            # Target path is outside workspace — try without workspace prefix
            resolved = resolve_path_safe(target_path)
        if resolved is None:
            return f"❌ Error: Path '{target_path}' is outside the workspace boundary."

        if new_path is None:
            # File deletion
            if not os.path.isfile(resolved):
                return f"❌ Error: Cannot delete '{target_path}': file not found."
            pending.append((resolved, open(resolved, "r", encoding="utf-8", errors="ignore").read(), None))
            continue

        if old_path is None:
            # New file creation
            if rec["hunks"]:
                new_lines = []
                for hunk in rec["hunks"]:
                    for hline in hunk["lines"]:
                        if hline.startswith("+"):
                            new_lines.append(hline[1:])
                new_content = "\n".join(new_lines)
                # Preserve trailing newline if last addition had one
                if new_lines and patch.rstrip().endswith("\n"):
                    new_content += "\n"
            else:
                new_content = ""
            pending.append((resolved, None, new_content))
            continue

        # Modification: read existing file, apply hunks
        if not os.path.isfile(resolved):
            return f"❌ Error: File '{target_path}' not found."
        try:
            with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
                original = f.read()
        except Exception as e:
            return f"❌ Error reading '{target_path}': {e}"

        # Normalise to LF for hunk application; restore original line ending on write
        original_le = "\r\n" if "\r\n" in original else "\n"
        original_lf = original.replace("\r\n", "\n")
        original_lines = original_lf.splitlines()

        try:
            new_lines = _apply_hunks(original_lines, rec["hunks"])
        except Exception as e:
            return f"❌ Error applying hunks to '{target_path}': {e}"

        new_lf = "\n".join(new_lines)
        # Restore trailing newline if original had one
        if original_lf.endswith("\n") and not new_lf.endswith("\n"):
            new_lf += "\n"

        new_content = new_lf.replace("\n", original_le)
        pending.append((resolved, original, new_content))

    # ── Phase 2: Write atomically with rollback ──────────────
    written: list[tuple[str, str | None]] = []  # (resolved_path, original_or_None)
    patched_files: list[str] = []

    for resolved, original, new_content in pending:
        try:
            if new_content is None:
                # Delete file
                os.remove(resolved)
                written.append((resolved, original))
                patched_files.append(f"  🗑  {resolved} (deleted)")
            else:
                # Create parent dirs if needed
                parent = os.path.dirname(resolved)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                # newline="" prevents OS-level \n→\r\n conversion on Windows;
                # line endings are already correct (LF or CRLF as per original).
                with open(resolved, "w", encoding="utf-8", newline="") as f:
                    f.write(new_content)
                written.append((resolved, original))
                action = "created" if original is None else "patched"
                patched_files.append(f"  ✅ {resolved} ({action})")

                # Store for frontend diff view
                if original is not None:
                    PENDING_DIFFS[resolved] = {"original": original, "modified": new_content}

        except Exception as e:
            # Roll back already-written files
            for r_path, orig in written:
                try:
                    if orig is None:
                        if os.path.isfile(r_path):
                            os.remove(r_path)
                    else:
                        with open(r_path, "w", encoding="utf-8", newline="") as f:
                            f.write(orig)
                except Exception:
                    pass
            return (
                f"❌ Write failed for '{resolved}': {e}. "
                f"Rolled back {len(written)} file(s)."
            )

    return f"✅ Patch applied to {len(patched_files)} file(s):\n" + "\n".join(patched_files)
