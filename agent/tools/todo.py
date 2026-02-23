"""
Todo tool — session-scoped task tracking.
Inspired by OpenCode's TodoRead/TodoWrite tools.

The agent uses this to track complex multi-step tasks,
giving the user visibility into progress.
"""
import json
import logging
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Session-scoped in-memory store (keyed by thread_id)
_todo_store: dict[str, list[dict]] = {}


def _get_todos(thread_id: str = "default") -> list[dict]:
    return _todo_store.get(thread_id, [])


def _set_todos(todos: list[dict], thread_id: str = "default"):
    _todo_store[thread_id] = todos


class TodoWriteArgs(BaseModel):
    todos: list[dict] = Field(
        description=(
            "Full list of todo items. Each item must have: "
            "'id' (int), 'content' (str), 'status' (pending|in_progress|completed|cancelled), "
            "'priority' (high|medium|low, optional). "
            "Pass the COMPLETE list — it replaces the current list."
        )
    )


@tool
def todo_read() -> str:
    """Read the current todo list for the session.

    Use this PROACTIVELY and FREQUENTLY:
    - At the beginning of conversations to see what's pending
    - Before starting new tasks to prioritize work
    - After completing tasks to update your understanding
    - Whenever you're uncertain about what to do next

    Takes no parameters. Returns list of todo items with status and priority.
    """
    todos = _get_todos()
    if not todos:
        return "No todos yet. Use todo_write to create tasks."

    lines = []
    status_icons = {
        "pending": "⬜",
        "in_progress": "🔄",
        "completed": "✅",
        "cancelled": "❌",
    }
    for t in todos:
        icon = status_icons.get(t.get("status", "pending"), "⬜")
        priority = t.get("priority", "")
        pri_label = f" [{priority.upper()}]" if priority else ""
        lines.append(f"{icon} {t['id']}. {t['content']}{pri_label}")

    summary = f"Total: {len(todos)} | "
    for s in ["pending", "in_progress", "completed", "cancelled"]:
        count = sum(1 for t in todos if t.get("status") == s)
        if count:
            summary += f"{s}: {count}  "

    return summary + "\n" + "\n".join(lines)


@tool(args_schema=TodoWriteArgs)
def todo_write(todos: list[dict]) -> str:
    """Create or update the todo list for the session.

    Use this to track complex multi-step tasks. When to use:
    - Task requires 3+ distinct steps
    - User provides a list of things to do
    - After discovering new sub-tasks during work
    - To mark tasks complete as you finish them

    When NOT to use:
    - Single trivial task
    - Purely conversational/informational request

    Task states: pending, in_progress, completed, cancelled
    - Only ONE task should be in_progress at a time
    - Mark tasks complete IMMEDIATELY after finishing

    Args:
        todos: Complete list of todo items (replaces current list).
    """
    # Validate
    valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
    for t in todos:
        if "id" not in t or "content" not in t:
            return "Error: Each todo must have 'id' and 'content' fields."
        if "status" not in t:
            t["status"] = "pending"
        if t["status"] not in valid_statuses:
            return f"Error: Invalid status '{t['status']}'. Use: {valid_statuses}"

    _set_todos(todos)
    logger.info(f"Todo list updated: {len(todos)} items")

    # Return summary
    completed = sum(1 for t in todos if t["status"] == "completed")
    total = len(todos)
    return f"Todo list updated: {completed}/{total} completed."
