"""
Universal tool output truncation layer.
Inspired by OpenCode's truncation system — caps all tool outputs
to prevent context window explosion.

Every tool output passes through truncate_output() which:
1. Limits by line count (MAX_LINES)
2. Limits by byte size (MAX_BYTES)  
3. Saves full output to disk when truncated
4. Returns truncated content with hint on how to access full output
"""
import os
import time
import hashlib
from pathlib import Path
import config

MAX_LINES = 2000
MAX_BYTES = 50 * 1024  # 50KB hard cap per tool output
MAX_LINE_LENGTH = 2000  # truncate individual lines longer than this

# Directory to store full outputs when truncated
TRUNCATED_DIR = config.DATA_DIR / "tool_output"
TRUNCATED_DIR.mkdir(exist_ok=True)

# Retention: clean up files older than 7 days
RETENTION_SECONDS = 7 * 24 * 60 * 60


def truncate_output(
    text: str,
    max_lines: int = MAX_LINES,
    max_bytes: int = MAX_BYTES,
    direction: str = "head",  # "head" = keep first N, "tail" = keep last N
) -> str:
    """Truncate tool output to stay within context budget.

    Args:
        text: Raw tool output string.
        max_lines: Maximum lines to keep.
        max_bytes: Maximum bytes to keep.
        direction: "head" keeps first lines, "tail" keeps last lines.

    Returns:
        Original text if within limits, otherwise truncated with a hint.
    """
    if not text:
        return text

    lines = text.split("\n")
    total_bytes = len(text.encode("utf-8", errors="replace"))

    # Truncate individual long lines first
    lines = [
        line[:MAX_LINE_LENGTH] + "..." if len(line) > MAX_LINE_LENGTH else line
        for line in lines
    ]

    # Check if within limits
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return text

    # Build truncated output
    out = []
    byte_count = 0
    hit_bytes = False

    if direction == "head":
        for i, line in enumerate(lines):
            if i >= max_lines:
                break
            line_bytes = len(line.encode("utf-8", errors="replace")) + (1 if i > 0 else 0)
            if byte_count + line_bytes > max_bytes:
                hit_bytes = True
                break
            out.append(line)
            byte_count += line_bytes
    else:  # tail
        for i in range(len(lines) - 1, -1, -1):
            if len(out) >= max_lines:
                break
            line_bytes = len(lines[i].encode("utf-8", errors="replace")) + (1 if out else 0)
            if byte_count + line_bytes > max_bytes:
                hit_bytes = True
                break
            out.insert(0, lines[i])
            byte_count += line_bytes

    # Calculate how much was removed
    removed = (total_bytes - byte_count) if hit_bytes else (len(lines) - len(out))
    unit = "bytes" if hit_bytes else "lines"

    # Save full output to disk
    output_path = _save_full_output(text)
    preview = "\n".join(out)

    hint = (
        f"Output truncated. Full output saved to: {output_path}\n"
        f"Use grep_search or file_read with line ranges to access specific sections."
    )

    if direction == "head":
        return f"{preview}\n\n...{removed} {unit} truncated...\n\n{hint}"
    else:
        return f"...{removed} {unit} truncated...\n\n{hint}\n\n{preview}"


def _save_full_output(text: str) -> str:
    """Save full tool output to a temp file and return the path."""
    ts = int(time.time() * 1000)
    h = hashlib.md5(text[:1000].encode(errors="replace")).hexdigest()[:8]
    filename = f"tool_{ts}_{h}.txt"
    filepath = TRUNCATED_DIR / filename

    try:
        filepath.write_text(text, encoding="utf-8", errors="replace")
        # Restrict permissions on Unix (owner-only read/write)
        try:
            filepath.chmod(0o600)
        except OSError:
            pass  # Windows doesn't support Unix permissions
    except Exception:
        return "(failed to save full output)"

    return str(filepath)


def cleanup_old_outputs():
    """Remove tool output files older than RETENTION_SECONDS."""
    if not TRUNCATED_DIR.exists():
        return

    cutoff = time.time() - RETENTION_SECONDS
    for f in TRUNCATED_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except Exception:
                pass


def estimate_tokens(text: str) -> int:
    """Estimate token count from text.

    Uses tiktoken for accuracy if available, otherwise a heuristic
    that counts words + punctuation (more accurate than simple len/4).
    """
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        pass
    # Heuristic: ~0.75 tokens per word for English/code, plus punctuation tokens
    words = text.split()
    # Code has many single-char tokens (brackets, operators), count them
    non_alnum = sum(1 for c in text if not c.isalnum() and not c.isspace())
    return max(1, len(words) + non_alnum // 2)
