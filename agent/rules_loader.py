"""Hierarchical CLAUDE.md / rules loader.

Discovery order (lowest → highest priority):
  1. ~/.shadowdev/CLAUDE.md          — global user rules
  2. {cwd}/CLAUDE.md                 — project root, team-shared
  3. {cwd}/.shadowdev/CLAUDE.md      — project local, personal
  4. {cwd}/.shadowdev/rules/*.md     — all .md files in rules dir, sorted by name

Supports @include directives: @include path/to/other.md (relative to the file
containing the directive, or absolute). Circular includes are detected and skipped.
HTML comments (<!-- ... -->) are stripped from final output.
Non-text file extensions are blocked from @include.
"""
import functools
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# File extensions that are safe to @include (text only)
_TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml", ".json",
    ".py", ".ts", ".js", ".sh", ".bash", ".zsh", ".cfg", ".ini", ".conf",
}

# Match HTML comments (strip from output)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _process_includes(content: str, base_dir: Path, visited: set) -> str:
    """Process @include directives recursively. Prevents circular includes.

    Also strips HTML comments from the final merged output.

    Args:
        content:  File text to process.
        base_dir: Directory of the file (for relative include resolution).
        visited:  Set of resolved absolute paths already visited (circular detection).

    Returns:
        Processed text with @include lines replaced by file contents.
    """
    lines = []
    for line in content.split("\n"):
        m = re.match(r"^@include\s+(.+)$", line.strip())
        if m:
            inc_path = Path(m.group(1).strip())
            if not inc_path.is_absolute():
                inc_path = base_dir / inc_path
            try:
                inc_real = inc_path.resolve()
            except OSError:
                lines.append(f"<!-- @include {inc_path}: resolution failed, skipped -->")
                continue

            # Block non-text extensions
            if inc_real.suffix.lower() not in _TEXT_EXTENSIONS:
                lines.append(
                    f"<!-- @include {inc_path}: blocked (non-text extension '{inc_real.suffix}') -->"
                )
                continue

            if str(inc_real) in visited:
                lines.append(f"<!-- @include {inc_path}: circular, skipped -->")
                continue

            visited.add(str(inc_real))

            if inc_real.is_file():
                try:
                    inc_content = inc_real.read_text(encoding="utf-8")
                    lines.append(_process_includes(inc_content, inc_real.parent, visited))
                except OSError as e:
                    lines.append(f"<!-- @include {inc_path}: read error ({e}), skipped -->")
            else:
                lines.append(f"<!-- @include {inc_path}: file not found, skipped -->")
        else:
            lines.append(line)

    result = "\n".join(lines)
    # Strip HTML comments from the merged output
    result = _HTML_COMMENT_RE.sub("", result)
    return result


def load_project_rules(workspace: str = "") -> str:
    """Load and concatenate all applicable rules files.

    Returns empty string if no files are found or all are empty.
    Results are cached per (resolved-cwd, mtime-signature) to avoid
    re-reading on every agent call.
    """
    cwd = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    cwd_str = str(cwd)

    # Build mtime signature from all candidate paths that exist
    home = Path.home()
    candidates = _candidate_paths(cwd, home)
    mtime_sig = _mtime_signature(candidates)

    return _cached_rules(cwd_str, mtime_sig)


def _candidate_paths(cwd: Path, home: Path) -> list:
    """Return ordered list of candidate rule file paths."""
    rule_glob = sorted((cwd / ".shadowdev" / "rules").glob("*.md")) \
        if (cwd / ".shadowdev" / "rules").is_dir() else []

    return [
        home / ".shadowdev" / "CLAUDE.md",
        cwd / "CLAUDE.md",
        cwd / ".shadowdev" / "CLAUDE.md",
        *rule_glob,
    ]


def _mtime_signature(candidates: list) -> str:
    """Build a cache key from the mtimes of all existing candidate files."""
    parts = []
    for p in candidates:
        try:
            if p.is_file():
                parts.append(f"{p}:{p.stat().st_mtime_ns}")
        except OSError:
            pass
    return "|".join(parts) if parts else "empty"


@functools.lru_cache(maxsize=16)
def _cached_rules(cwd: str, mtime_sig: str) -> str:  # noqa: ARG001 — mtime_sig is the cache key
    """Read and merge all rules files for the given workspace.

    The mtime_sig parameter is intentionally unused in the body — its sole
    purpose is to bust the lru_cache when any source file changes.
    Supports @include directives and strips HTML comments.
    """
    cwd_path = Path(cwd)
    home = Path.home()
    candidates = _candidate_paths(cwd_path, home)

    sections = []
    for p in candidates:
        try:
            if not p.is_file():
                continue
            raw = p.read_text(encoding="utf-8")
            # Process @include directives (circular detection per-file chain)
            visited: set = {str(p.resolve())}
            content = _process_includes(raw, p.parent, visited).strip()
            if content:
                sections.append(f"## Rules from {p.name}\n{content}")
        except OSError:
            pass

    return "\n\n".join(sections)
