"""
agent/skill_loader.py — Zero-config skill (plugin) loader.

Drop any *.py file into the skills/ directory at the project root,
restart the server, and the tools it defines are automatically registered.
No configuration files, no imports to edit.

Skill file contract
-------------------
A skill file must define at least one attribute:

    __skill_tools__ = [my_tool, ...]      # REQUIRED — list of @tool objects

Optional metadata attributes:

    __skill_name__    = "My Skill"        # display name in startup log
    __skill_version__ = "1.0"             # version string (informational)
    __skill_access__  = "read"            # access level:
                                          #   "read"  (default) → PLANNER_TOOLS + CODER_TOOLS
                                          #   "write"           → CODER_TOOLS only

Failure behaviour
-----------------
- skills/ directory missing  → auto-created, 0 skills loaded, agent continues
- file has syntax error       → warning logged, file skipped
- __skill_tools__ missing     → warning logged, file skipped
- tool name already taken     → warning logged, that tool skipped, others load
- any other exception         → warning logged, file skipped, agent continues

None of these failures raise an exception or prevent the agent from starting.
"""

import importlib.util
import logging
import sys
import traceback
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# skills/ lives at the project root (one level above agent/)
SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Python tool plugins live in skills/_tools/
# (keeps .py files separate from .md workflow skill files)
TOOLS_SUBDIR = SKILLS_DIR / "_tools"

_VALID_ACCESS = ("read", "write")


def load_skills(
    existing_names: Optional[set] = None,
) -> tuple[list, list]:
    """Scan skills/ and load all valid skill files.

    Args:
        existing_names: Set of tool names already registered as core tools.
            Skill tools whose names appear in this set are skipped with a warning.

    Returns:
        (planner_skills, coder_only_skills) — lists of @tool objects ready to
        be appended to PLANNER_TOOLS and CODER_TOOLS respectively.
    """
    existing_names = set(existing_names or [])

    # Auto-create directories on first run so users know where to put skills
    for d in (SKILLS_DIR, TOOLS_SUBDIR):
        if not d.exists():
            try:
                d.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Created skills directory: {d}")
            except OSError as e:
                logger.warning(f"Could not create skills directory {d}: {e}")

    if not TOOLS_SUBDIR.exists():
        return [], []

    skill_files = sorted(TOOLS_SUBDIR.glob("*.py"))
    # Skip __init__.py, _private.py, etc.
    skill_files = [f for f in skill_files if not f.name.startswith("_")]

    if not skill_files:
        return [], []

    planner_skills: list = []
    coder_only_skills: list = []
    seen_names: set = set(existing_names)  # grows as we load each skill

    loaded_files = 0
    total_tools = 0

    for skill_file in skill_files:
        try:
            new_planner, new_coder, n_tools = _load_one_skill(
                skill_file, seen_names
            )
        except Exception as e:
            logger.warning(
                f"[skills] '{skill_file.name}' failed to load — skipped. "
                f"Error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
            )
            continue

        if n_tools == 0:
            continue

        planner_skills.extend(new_planner)
        coder_only_skills.extend(new_coder)
        # Update seen_names so later skills can't duplicate earlier ones
        for t in new_planner + new_coder:
            seen_names.add(t.name)

        loaded_files += 1
        total_tools += n_tools

    if total_tools > 0:
        print(
            f"[skills] Loaded: {total_tools} tool(s) from "
            f"{loaded_files} file(s) in {TOOLS_SUBDIR}"
        )

    return planner_skills, coder_only_skills


def _load_one_skill(
    path: Path,
    seen_names: set,
) -> tuple[list, list, int]:
    """Import a single skill file and extract its tools.

    Returns:
        (planner_tools, coder_only_tools, total_count)

    Raises:
        Any exception from importlib or user code — caller must catch.
    """
    # Use a unique module name to avoid collisions in sys.modules
    module_name = f"_skill_{path.stem}"

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {path}")

    module = importlib.util.module_from_spec(spec)

    # Register temporarily so relative imports inside the skill work
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise

    # Read __skill_tools__
    raw_tools = getattr(module, "__skill_tools__", None)
    if raw_tools is None:
        logger.warning(
            f"[skills] '{path.name}' has no __skill_tools__ attribute — skipped"
        )
        return [], [], 0

    if not isinstance(raw_tools, (list, tuple)):
        logger.warning(
            f"[skills] '{path.name}': __skill_tools__ must be a list, "
            f"got {type(raw_tools).__name__} — skipped"
        )
        return [], [], 0

    # Read metadata
    access = getattr(module, "__skill_access__", "read")
    skill_name = getattr(module, "__skill_name__", path.stem)
    skill_version = getattr(module, "__skill_version__", "")

    if access not in _VALID_ACCESS:
        logger.warning(
            f"[skills] '{path.name}': unknown __skill_access__='{access}', "
            f"defaulting to 'read'"
        )
        access = "read"

    version_str = f" v{skill_version}" if skill_version else ""
    logger.info(f"[skills] Loading '{skill_name}'{version_str} ({path.name})")

    planner_tools: list = []
    coder_tools: list = []
    loaded = 0

    for tool_obj in raw_tools:
        # Get the tool's registered name (LangChain @tool sets .name)
        tool_name = getattr(tool_obj, "name", None)
        if tool_name is None:
            # Fallback: function __name__
            tool_name = getattr(tool_obj, "__name__", repr(tool_obj))

        if tool_name in seen_names:
            logger.warning(
                f"[skills] '{path.name}': tool '{tool_name}' conflicts with an "
                f"existing tool name — skipped"
            )
            continue

        # LangChain tools implement .invoke() — callable() is unreliable for them
        if not hasattr(tool_obj, "invoke"):
            logger.warning(
                f"[skills] '{path.name}': '{tool_name}' is not a valid tool "
                f"(missing .invoke) — skipped"
            )
            continue

        if access == "write":
            coder_tools.append(tool_obj)
        else:
            planner_tools.append(tool_obj)

        seen_names.add(tool_name)
        loaded += 1
        logger.debug(f"[skills]   + {tool_name} (access={access})")

    if loaded > 0:
        logger.info(
            f"[skills] '{skill_name}': {loaded} tool(s) registered "
            f"(access={access})"
        )

    return planner_tools, coder_tools, loaded
