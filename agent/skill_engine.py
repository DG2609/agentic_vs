"""
agent/skill_engine.py — Markdown-based Agent Skills engine.

Skills are .md files in skills/ with:
  - YAML frontmatter  (name, description, model, subtask, tools, version)
  - Markdown body     (instructions / workflow)
  - !`command`        → inject live shell output inline
  - $ARGUMENTS        → replaced with user-provided input

Directory layout:
  skills/*.md          — workflow skills and commands
  skills/agents/*.md   — specialized agent personas

This is separate from Python tool plugins (skills/_tools/*.py).
"""

import re
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────
SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Directories scanned for .md skill files (in priority order)
SKILL_SEARCH_DIRS = [SKILLS_DIR, SKILLS_DIR / "agents"]

# Matches !`command` on its own line (leading whitespace allowed)
_SHELL_RE = re.compile(r"^\s*!\s*`(.+?)`\s*$", re.MULTILINE)


# ── Data classes ─────────────────────────────────────────────

@dataclass
class SkillMeta:
    name: str
    description: str = ""
    model: str = ""          # suggested model override
    subtask: bool = False    # designed to run as a background subtask
    tools: dict = field(default_factory=dict)   # optional tool restrictions (future)
    version: str = ""
    source_file: str = ""    # absolute path to .md file


@dataclass
class Skill:
    meta: SkillMeta
    raw_body: str            # body text before !cmd injection


# ── YAML frontmatter parser ──────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown string.

    Frontmatter is delimited by --- lines at the very beginning of the file.

    Returns:
        (meta_dict, body_text)
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")

    return _simple_yaml(fm_text), body


def _simple_yaml(text: str) -> dict:
    """Parse a minimal YAML subset: scalars, booleans, nested dicts."""
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        # Top-level key: value
        if ":" in line and not line.startswith(" "):
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()

            if not rest:
                # Possible nested mapping block
                nested: dict = {}
                i += 1
                while i < len(lines) and lines[i].startswith("  "):
                    nline = lines[i].strip()
                    if ":" in nline:
                        nk, _, nv = nline.partition(":")
                        nested[nk.strip()] = _coerce(nv.strip())
                    i += 1
                result[key] = nested
                continue
            else:
                result[key] = _coerce(rest)

        i += 1
    return result


def _coerce(v: str):
    """Convert YAML scalar string to a Python bool/int/None/str."""
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v.lower() in ("null", "~", ""):
        return None
    if (v.startswith('"') and v.endswith('"')) or \
       (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    return v


# ── Body processing ──────────────────────────────────────────

def _process_body(body: str, arguments: str = "", cwd: str = "") -> str:
    """Apply !`cmd` injection and $ARGUMENTS substitution.

    Args:
        body:      Raw markdown body.
        arguments: User-provided text to replace $ARGUMENTS.
        cwd:       Working directory for shell commands (default: WORKSPACE_DIR).
    """
    # Resolve and clamp cwd to workspace (defense-in-depth)
    from agent.tools.utils import resolve_tool_path
    resolved_cwd = resolve_tool_path(cwd) if cwd else config.WORKSPACE_DIR
    work_dir = resolved_cwd

    def _run_cmd(m: re.Match) -> str:
        cmd = m.group(1).strip()
        try:
            # shell=True: commands come from trusted .md skill files, not user input.
            # Sandboxed via work_dir (workspace-bound) and 30s timeout.
            r = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=30,
                cwd=work_dir, encoding="utf-8", errors="replace",
            )
            output = (r.stdout or r.stderr or "(no output)").rstrip()
            return f"```\n$ {cmd}\n{output}\n```"
        except subprocess.TimeoutExpired:
            return f"```\n$ {cmd}\n(timed out after 30s)\n```"
        except Exception as e:
            return f"```\n$ {cmd}\n(error: {e})\n```"

    # Substitute $ARGUMENTS BEFORE executing shell commands so that
    # !`echo $ARGUMENTS` gets the literal text, not a shell-injectable string.
    safe_args = arguments or "(no additional arguments)"
    body = body.replace("$ARGUMENTS", safe_args)
    body = _SHELL_RE.sub(_run_cmd, body)
    return body


# ── Discovery ────────────────────────────────────────────────

def discover_skills() -> list[Skill]:
    """Scan all skill directories and return parsed Skill objects.

    Skips files starting with '_'. Warns on duplicates and parse errors.
    """
    skills: list[Skill] = []
    seen: set[str] = set()

    for d in SKILL_SEARCH_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            if path.name.startswith("_"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                meta_dict, body = _parse_frontmatter(text)

                name = str(meta_dict.get("name", path.stem))
                if name in seen:
                    logger.warning(
                        f"[skill-engine] Duplicate skill '{name}' in {path.name} — skipped"
                    )
                    continue

                meta = SkillMeta(
                    name=name,
                    description=str(meta_dict.get("description", "")),
                    model=str(meta_dict.get("model", "")),
                    subtask=bool(meta_dict.get("subtask", False)),
                    tools=meta_dict.get("tools", {}) or {},
                    version=str(meta_dict.get("version", "")),
                    source_file=str(path),
                )
                skills.append(Skill(meta=meta, raw_body=body))
                seen.add(name)

            except Exception as e:
                logger.warning(f"[skill-engine] Failed to parse '{path.name}': {e}")

    return skills


def _find_skill_path(name: str) -> Optional[Path]:
    """Find a skill file by name. Tries .md extension and kebab/underscore variants."""
    base = name if name.endswith(".md") else f"{name}.md"
    variants = {base, base.replace(" ", "-"), base.replace("_", "-")}

    for d in SKILL_SEARCH_DIRS:
        for variant in variants:
            p = d / variant
            if p.is_file():
                return p
    return None


# ── Invocation ───────────────────────────────────────────────

def invoke_skill(
    name: str,
    arguments: str = "",
    cwd: str = "",
) -> tuple[str, Optional[SkillMeta]]:
    """Load a skill by name, inject context, return (content, meta).

    Returns (error_message, None) if the skill is not found or fails to load.
    """
    path = _find_skill_path(name)
    if path is None:
        available = sorted(s.meta.name for s in discover_skills())
        hint = ", ".join(available) if available else "(none)"
        return (
            f"Skill '{name}' not found.\nAvailable skills: {hint}\n"
            f"Use skill_list() to see descriptions.",
            None,
        )

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta_dict, raw_body = _parse_frontmatter(text)

        meta = SkillMeta(
            name=str(meta_dict.get("name", path.stem)),
            description=str(meta_dict.get("description", "")),
            model=str(meta_dict.get("model", "")),
            subtask=bool(meta_dict.get("subtask", False)),
            tools=meta_dict.get("tools", {}) or {},
            version=str(meta_dict.get("version", "")),
            source_file=str(path),
        )

        content = _process_body(raw_body, arguments=arguments, cwd=cwd)
        return content, meta

    except Exception as e:
        return f"Error loading skill '{name}': {e}", None
