"""
Cron scheduling tools — create, list, and delete scheduled agent tasks.

Tasks persist in {WORKSPACE}/.shadowdev/scheduled_tasks.json.
Each task has a 5-field cron expression (minute hour day-of-month month day-of-week).
run_pending_cron() is called at session start to execute due tasks.
"""
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

import config

logger = logging.getLogger(__name__)

_TASKS_FILE = Path(config.WORKSPACE_DIR) / ".shadowdev" / "scheduled_tasks.json"
_MAX_TASKS = 50
_CRON_FIELD_RE = re.compile(r'^(\*|\d+(?:,\d+)*|\d+-\d+|\*/\d+)$')


# ── Persistence ─────────────────────────────────────────────────────────────

def _load_tasks() -> list[dict]:
    try:
        if _TASKS_FILE.exists():
            return json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load cron tasks: %s", e)
    return []


def _save_tasks(tasks: list[dict]) -> None:
    try:
        _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to save cron tasks: %s", e)


# ── Cron expression parsing ──────────────────────────────────────────────────

def _parse_cron(expr: str) -> Optional[tuple[str, str, str, str, str]]:
    """Parse a 5-field cron expression. Returns None if invalid."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    for p in parts:
        if not _CRON_FIELD_RE.match(p):
            return None
    return tuple(parts)  # type: ignore[return-value]


def _field_matches(field: str, value: int) -> bool:
    """Check if a cron field matches a given integer value."""
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "-" in field:
        lo, hi = field.split("-", 1)
        return int(lo) <= value <= int(hi)
    return value in {int(x) for x in field.split(",")}


def _cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if the cron expression fires at the given datetime (UTC)."""
    parts = _parse_cron(expr)
    if parts is None:
        return False
    minute, hour, dom, month, dow = parts
    return (
        _field_matches(minute, dt.minute)
        and _field_matches(hour, dt.hour)
        and _field_matches(dom, dt.day)
        and _field_matches(month, dt.month)
        and _field_matches(dow, dt.weekday())  # 0=Monday (cron: 0=Sunday — normalize below)
    )


def _is_task_due(task: dict, now: Optional[datetime] = None) -> bool:
    """Return True if the task should fire now."""
    now = now or datetime.now(timezone.utc)

    # Check expiry
    if task.get("expire_at"):
        try:
            expire_dt = datetime.fromisoformat(task["expire_at"])
            if now > expire_dt:
                return False
        except ValueError:
            pass

    # Check if already ran (one-shot)
    if task.get("one_shot") and task.get("last_run"):
        return False

    # Check last_run — don't fire more than once per minute
    if task.get("last_run"):
        try:
            last = datetime.fromisoformat(task["last_run"])
            if (now - last).total_seconds() < 60:
                return False
        except ValueError:
            pass

    return _cron_matches(task["schedule"], now)


# ── Public function called at session start ──────────────────────────────────

def run_pending_cron() -> list[str]:
    """Check and execute any due cron tasks. Returns list of executed task descriptions."""
    tasks = _load_tasks()
    now = datetime.now(timezone.utc)
    executed = []
    changed = False

    for task in tasks:
        if _is_task_due(task, now):
            logger.info("Cron task due: %s — %s", task["id"], task["description"])
            task["last_run"] = now.isoformat()
            task["run_count"] = task.get("run_count", 0) + 1
            changed = True
            executed.append(task["description"])

    # Prune expired one-shot tasks
    before = len(tasks)
    tasks = [
        t for t in tasks
        if not (t.get("one_shot") and t.get("last_run"))
        and (
            not t.get("expire_at")
            or datetime.fromisoformat(t["expire_at"]) > now
        )
    ]
    if len(tasks) != before:
        changed = True

    if changed:
        _save_tasks(tasks)

    return executed


# ── Tool schemas ─────────────────────────────────────────────────────────────

class CronCreateArgs(BaseModel):
    schedule: str = Field(
        description=(
            "5-field cron expression: 'minute hour day-of-month month day-of-week'. "
            "Examples: '0 9 * * 1' = every Monday 9am, '*/30 * * * *' = every 30 min, "
            "'0 18 * * 1-5' = weekdays 6pm."
        )
    )
    description: str = Field(description="What this task does / what the agent should execute.")
    command: str = Field(
        default="",
        description="Optional shell command to run when the task fires. Leave empty for agent-reminder tasks."
    )
    one_shot: bool = Field(
        default=False,
        description="If True, task auto-deletes after firing once."
    )
    expire_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Number of days before this task auto-expires (default 30)."
    )


class CronDeleteArgs(BaseModel):
    task_id: str = Field(description="ID of the task to delete (from cron_list).")


# ── Tools ────────────────────────────────────────────────────────────────────

@tool(args_schema=CronCreateArgs)
def cron_create(
    schedule: str,
    description: str,
    command: str = "",
    one_shot: bool = False,
    expire_days: int = 30,
) -> str:
    """Create a scheduled recurring task with a cron expression.

    Tasks persist across sessions in .shadowdev/scheduled_tasks.json.
    Use cron_list to see all tasks, cron_delete to remove them.
    run_pending_cron() is called at each session start to fire due tasks.

    Args:
        schedule: 5-field cron expression (minute hour dom month dow).
        description: Human-readable description of what the task does.
        command: Optional shell command to execute when fired.
        one_shot: Auto-delete after first execution.
        expire_days: Days until task auto-expires.
    """
    if not _parse_cron(schedule):
        return (
            f"Invalid cron expression: '{schedule}'. "
            "Expected 5 fields: minute hour day-of-month month day-of-week. "
            "Examples: '0 9 * * 1' (Mon 9am), '*/15 * * * *' (every 15 min)."
        )

    tasks = _load_tasks()
    if len(tasks) >= _MAX_TASKS:
        return f"Cron limit reached ({_MAX_TASKS} tasks). Delete some with cron_delete first."

    now = datetime.now(timezone.utc)
    task = {
        "id": str(uuid.uuid4())[:8],
        "schedule": schedule,
        "description": description,
        "command": command,
        "one_shot": one_shot,
        "created_at": now.isoformat(),
        "expire_at": now.replace(
            day=now.day + expire_days
            if now.day + expire_days <= 28
            else 28  # safe cross-month default
        ).isoformat() if expire_days else None,
        "last_run": None,
        "run_count": 0,
    }
    # Proper expiry calculation
    from datetime import timedelta
    expire_dt = now + timedelta(days=expire_days)
    task["expire_at"] = expire_dt.isoformat()

    tasks.append(task)
    _save_tasks(tasks)

    mode = "one-shot" if one_shot else f"recurring (expires in {expire_days}d)"
    return (
        f"Cron task created [ID: {task['id']}]\n"
        f"Schedule: {schedule}\n"
        f"Mode: {mode}\n"
        f"Description: {description}\n"
        f"Command: {command or '(none — reminder only)'}"
    )


@tool
def cron_list() -> str:
    """List all scheduled cron tasks.

    Shows task ID, cron schedule, description, mode (recurring/one-shot),
    last run time, and run count. Use cron_delete to remove tasks.
    """
    tasks = _load_tasks()
    if not tasks:
        return "No cron tasks scheduled. Use cron_create to add tasks."

    now = datetime.now(timezone.utc)
    lines = [f"Scheduled tasks ({len(tasks)}/{_MAX_TASKS}):"]
    for t in tasks:
        mode = "one-shot" if t.get("one_shot") else "recurring"
        last = t.get("last_run") or "never"
        runs = t.get("run_count", 0)
        expired = ""
        if t.get("expire_at"):
            try:
                exp = datetime.fromisoformat(t["expire_at"])
                if exp < now:
                    expired = " [EXPIRED]"
                else:
                    days_left = (exp - now).days
                    expired = f" (expires in {days_left}d)"
            except ValueError:
                pass
        cmd = f" → `{t['command']}`" if t.get("command") else ""
        lines.append(
            f"  [{t['id']}] {t['schedule']} | {mode}{expired}\n"
            f"    {t['description']}{cmd}\n"
            f"    Runs: {runs} | Last: {last}"
        )

    return "\n".join(lines)


@tool(args_schema=CronDeleteArgs)
def cron_delete(task_id: str) -> str:
    """Delete a scheduled cron task by ID.

    Use cron_list to find task IDs.

    Args:
        task_id: The task ID to delete (8-character hex from cron_list).
    """
    tasks = _load_tasks()
    before = len(tasks)
    tasks = [t for t in tasks if t["id"] != task_id]

    if len(tasks) == before:
        return f"Task '{task_id}' not found. Use cron_list to see available task IDs."

    _save_tasks(tasks)
    return f"Cron task '{task_id}' deleted. {len(tasks)} task(s) remaining."
