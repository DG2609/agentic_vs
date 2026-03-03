"""
Example Skill — ShadowDev Agent Skills Template
================================================

HOW TO CREATE YOUR OWN SKILL
-----------------------------
1. Copy this file to a new name, e.g. `my_skill.py`
2. Define your tools with the @tool decorator
3. List them in __skill_tools__
4. Set __skill_access__ to "read" or "write"
5. Restart the server — your tools are live

SKILL METADATA (all optional except __skill_tools__)
-----------------------------------------------------
__skill_name__    = "Display name in startup log"
__skill_version__ = "1.0.0"
__skill_access__  = "read"   # "read"  → Planner + Coder can use the tools
                             # "write" → Coder only (use for tools that modify files/state)

TOOL RULES
----------
- @tool is from langchain_core.tools — same as all core tools
- The docstring IS what the LLM sees — write it clearly
- Tool name must be unique across all core tools and other skills
- Use truncate_output() to cap large results (built-in 50KB limit)
- Use resolve_tool_path() for safe file path resolution (stays inside workspace)
- Pydantic args_schema is optional but helps the LLM pass correct arguments

AVAILABLE IMPORTS (from the core system)
-----------------------------------------
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path
from agent.tools.file_ops import file_read   # can call other tools
import config                                 # config.WORKSPACE_DIR, config.TOOL_TIMEOUT, etc.
"""

# ── Skill metadata ────────────────────────────────────────────

__skill_name__    = "Example Skill"
__skill_version__ = "1.0.0"
__skill_access__  = "read"   # "read" = available to Planner + Coder
                             # "write" = Coder only


# ── Tool definitions ──────────────────────────────────────────

from langchain_core.tools import tool
from agent.tools.truncation import truncate_output


@tool
def echo_tool(message: str) -> str:
    """Echo a message back. Useful for testing that custom skills are loaded.

    Args:
        message: The text to echo.

    Returns:
        The same text with a confirmation prefix.
    """
    return truncate_output(f"[echo_tool] {message}")


# ── Example: tool with Pydantic args_schema ───────────────────
#
# Using a Pydantic schema is optional but improves LLM accuracy.
# Define the schema near the tool, not in models/tool_schemas.py
# (that file is for core tools only).
#
# from pydantic import BaseModel, Field
#
# class RepeatArgs(BaseModel):
#     text: str = Field(description="Text to repeat")
#     times: int = Field(default=2, ge=1, le=10, description="How many times (1-10)")
#
# @tool(args_schema=RepeatArgs)
# def repeat_tool(text: str, times: int = 2) -> str:
#     """Repeat a string N times."""
#     return truncate_output((text + "\n") * times)


# ── REQUIRED: list all tools this skill exposes ───────────────

__skill_tools__ = [
    echo_tool,
    # repeat_tool,   # uncomment if you add more tools above
]
