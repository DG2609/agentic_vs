"""
Plan tools — switch between plan mode and build mode.
Inspired by OpenCode's plan-enter/plan-exit tools.

Plan mode restricts the agent to read-only tools for research + planning.
"""
import os
import logging
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import config

logger = logging.getLogger(__name__)

PLAN_FILE = os.path.join(config.WORKSPACE_DIR, ".plan.md")


class PlanEnterArgs(BaseModel):
    reason: str = Field(
        description="Why planning is needed before implementation."
    )


class PlanExitArgs(BaseModel):
    summary: str = Field(
        default="",
        description="Brief summary of the plan created."
    )


@tool(args_schema=PlanEnterArgs)
def plan_enter(reason: str) -> str:
    """Suggest switching to plan mode for research and design before implementation.

    Call this when:
    - The user's request is complex and would benefit from planning first
    - You want to research and design before making changes
    - The task involves multiple files or significant architectural decisions
    - The user explicitly asks for a plan

    Do NOT call this for simple, straightforward tasks.

    Args:
        reason: Why planning is needed.
    """
    # Create or reset plan file
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Plan\n\n**Reason:** {reason}\n\n## Steps\n\n")

    logger.info(f"Plan mode entered: {reason}")
    return (
        f"📋 Plan mode activated.\n"
        f"Reason: {reason}\n"
        f"Plan file: .plan.md\n\n"
        f"Use file_edit to write your plan to .plan.md, then call plan_exit when ready."
    )


@tool(args_schema=PlanExitArgs)
def plan_exit(summary: str = "") -> str:
    """Exit plan mode and switch back to build mode for implementation.

    Call this after:
    - You have written a complete plan to .plan.md
    - You have clarified any questions with the user
    - You are confident the plan is ready for implementation

    Do NOT call this before the plan is finalized.

    Args:
        summary: Brief summary of the plan.
    """
    # Read the plan file to verify it exists
    if os.path.isfile(PLAN_FILE):
        with open(PLAN_FILE, "r", encoding="utf-8") as f:
            plan_content = f.read()
        plan_lines = len([l for l in plan_content.split("\n") if l.strip()])
    else:
        plan_lines = 0

    logger.info(f"Plan mode exited: {summary}")
    return (
        f"🔨 Switching to build mode.\n"
        f"Plan: {plan_lines} lines in .plan.md\n"
        f"Summary: {summary}\n\n"
        f"Ready to implement. Proceed with the plan."
    )
