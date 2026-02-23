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
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from models.tool_schemas import (
    FileReadArgs, FileWriteArgs, FileEditArgs, FileListArgs, GlobSearchArgs,
)


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
    resolved = _resolve_path(file_path)
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

    try:
        if create_dirs:
            os.makedirs(os.path.dirname(resolved), exist_ok=True)

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

    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    # Strategy 1: Exact match
    count = content.count(old_string)
    if count == 1:
        new_content = content.replace(old_string, new_string, 1)
        return _write_edit(resolved, file_path, content, new_content, "exact_match")
    elif count > 1:
        return f"❌ Error: 'old_string' found {count} times. Please provide more specific context."

    # Strategy 2: Line-trimmed match
    matched_text, info = _line_trimmed_match(content, old_string)
    if matched_text is not None:
        if content.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string, matched_text, new_string)
            new_content = content.replace(matched_text, adjusted_new, 1)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # Strategy 3: Levenshtein fuzzy match
    matched_text, info = _levenshtein_match(content, old_string)
    if matched_text is not None:
        if content.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string, matched_text, new_string)
            new_content = content.replace(matched_text, adjusted_new, 1)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # Strategy 4: Block anchor match
    matched_text, info = _block_anchor_match(content, old_string)
    if matched_text is not None:
        if content.count(matched_text) == 1:
            adjusted_new = _adjust_indentation(old_string, matched_text, new_string)
            new_content = content.replace(matched_text, adjusted_new, 1)
            return _write_edit(resolved, file_path, content, new_content, f"fuzzy:{info}")

    # All strategies failed — provide helpful error with closest match
    return _edit_failed_hint(content, old_string, file_path)


def _write_edit(resolved: str, file_path: str, old_content: str, new_content: str, strategy: str) -> str:
    """Write the edited content and return result with diff preview."""
    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return f"Error writing file: {e}"

    # Generate compact diff for confirmation
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    diff_preview = ""
    if diff:
        diff_lines = diff[:30]  # limit diff output
        diff_preview = "\n\nDiff:\n```\n" + "\n".join(diff_lines)
        if len(diff) > 30:
            diff_preview += f"\n... ({len(diff) - 30} more diff lines)"
        diff_preview += "\n```"

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
    target = directory or config.WORKSPACE_DIR
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
    target = directory or config.WORKSPACE_DIR
    if not os.path.isdir(target):
        return f"Error: Directory '{target}' does not exist."

    # Prevent escaping workspace
    # Python glob doesn't support root_dir until 3.10+, we assume modern python
    # But to be safe with older python, use os.chdir or join carefully
    # We'll use recursive glob from the target directory

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
        
    return truncate_output(f"Found {len(filtered)} files for '{pattern}':\n" + "\n".join(sorted(filtered)))


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
    """Resolve relative paths against workspace."""
    if os.path.isabs(p):
        return p
    return os.path.join(config.WORKSPACE_DIR, p)
