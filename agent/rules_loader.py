"""Hierarchical CLAUDE.md / rules loader.

Discovery order (lowest → highest priority):
  1. ~/.shadowdev/CLAUDE.md          — global user rules
  2. {cwd}/CLAUDE.md                 — project root, team-shared
  3. {cwd}/.shadowdev/CLAUDE.md      — project local, personal
  4. {cwd}/.shadowdev/rules/*.md     — all .md files in rules dir, sorted by name
"""
import functools
from pathlib import Path


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
    """
    cwd_path = Path(cwd)
    home = Path.home()
    candidates = _candidate_paths(cwd_path, home)

    sections = []
    for p in candidates:
        try:
            if not p.is_file():
                continue
            content = p.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"## Rules from {p.name}\n{content}")
        except OSError:
            pass

    return "\n\n".join(sections)
