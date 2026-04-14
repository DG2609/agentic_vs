"""Tests for cron scheduling tools."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_task(
    schedule="* * * * *",
    description="test task",
    one_shot=False,
    last_run=None,
    run_count=0,
    expire_at=None,
    task_id="abc12345",
    command="",
):
    now = datetime.now(timezone.utc)
    return {
        "id": task_id,
        "schedule": schedule,
        "description": description,
        "command": command,
        "one_shot": one_shot,
        "created_at": now.isoformat(),
        "expire_at": expire_at or (now + timedelta(days=30)).isoformat(),
        "last_run": last_run,
        "run_count": run_count,
    }


# ── _parse_cron ──────────────────────────────────────────────────────────────

def test_parse_cron_valid_wildcard():
    from agent.tools.cron import _parse_cron
    result = _parse_cron("* * * * *")
    assert result == ("*", "*", "*", "*", "*")


def test_parse_cron_valid_specific():
    from agent.tools.cron import _parse_cron
    result = _parse_cron("0 9 * * 1")
    assert result == ("0", "9", "*", "*", "1")


def test_parse_cron_valid_step():
    from agent.tools.cron import _parse_cron
    result = _parse_cron("*/15 * * * *")
    assert result is not None


def test_parse_cron_valid_range():
    from agent.tools.cron import _parse_cron
    result = _parse_cron("0 9-17 * * 1-5")
    assert result is not None


def test_parse_cron_invalid_too_few_fields():
    from agent.tools.cron import _parse_cron
    assert _parse_cron("* * * *") is None


def test_parse_cron_invalid_too_many_fields():
    from agent.tools.cron import _parse_cron
    assert _parse_cron("* * * * * *") is None


def test_parse_cron_invalid_field():
    from agent.tools.cron import _parse_cron
    assert _parse_cron("foo * * * *") is None


# ── _field_matches ───────────────────────────────────────────────────────────

def test_field_matches_wildcard():
    from agent.tools.cron import _field_matches
    assert _field_matches("*", 0)
    assert _field_matches("*", 59)


def test_field_matches_exact():
    from agent.tools.cron import _field_matches
    assert _field_matches("5", 5)
    assert not _field_matches("5", 6)


def test_field_matches_step():
    from agent.tools.cron import _field_matches
    assert _field_matches("*/15", 0)
    assert _field_matches("*/15", 15)
    assert not _field_matches("*/15", 7)


def test_field_matches_range():
    from agent.tools.cron import _field_matches
    assert _field_matches("9-17", 12)
    assert not _field_matches("9-17", 8)
    assert not _field_matches("9-17", 18)


def test_field_matches_list():
    from agent.tools.cron import _field_matches
    assert _field_matches("1,3,5", 3)
    assert not _field_matches("1,3,5", 4)


# ── _is_task_due ─────────────────────────────────────────────────────────────

def test_is_task_due_wildcard_due():
    from agent.tools.cron import _is_task_due
    task = _make_task("* * * * *")
    now = datetime.now(timezone.utc)
    assert _is_task_due(task, now)


def test_is_task_due_one_shot_already_ran():
    from agent.tools.cron import _is_task_due
    last = datetime.now(timezone.utc) - timedelta(hours=1)
    task = _make_task("* * * * *", one_shot=True, last_run=last.isoformat())
    assert not _is_task_due(task, datetime.now(timezone.utc))


def test_is_task_due_too_soon():
    from agent.tools.cron import _is_task_due
    last = datetime.now(timezone.utc) - timedelta(seconds=30)
    task = _make_task("* * * * *", last_run=last.isoformat())
    assert not _is_task_due(task, datetime.now(timezone.utc))


def test_is_task_due_expired():
    from agent.tools.cron import _is_task_due
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    task = _make_task("* * * * *", expire_at=expired)
    assert not _is_task_due(task, datetime.now(timezone.utc))


# ── cron_create tool ─────────────────────────────────────────────────────────

def test_cron_create_valid(tmp_path):
    from agent.tools.cron import cron_create
    with patch("agent.tools.cron._TASKS_FILE", tmp_path / "tasks.json"):
        result = cron_create.invoke({
            "schedule": "0 9 * * 1",
            "description": "Weekly review",
        })
    assert "Weekly review" in result
    assert "0 9 * * 1" in result


def test_cron_create_invalid_schedule(tmp_path):
    from agent.tools.cron import cron_create
    with patch("agent.tools.cron._TASKS_FILE", tmp_path / "tasks.json"):
        result = cron_create.invoke({
            "schedule": "not-a-cron",
            "description": "bad task",
        })
    assert "Invalid cron expression" in result


def test_cron_create_persists_to_file(tmp_path):
    from agent.tools.cron import cron_create
    tasks_file = tmp_path / "tasks.json"
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        cron_create.invoke({"schedule": "* * * * *", "description": "test"})
    tasks = json.loads(tasks_file.read_text())
    assert len(tasks) == 1
    assert tasks[0]["description"] == "test"


def test_cron_create_one_shot_flag(tmp_path):
    from agent.tools.cron import cron_create
    tasks_file = tmp_path / "tasks.json"
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        result = cron_create.invoke({
            "schedule": "* * * * *",
            "description": "once only",
            "one_shot": True,
        })
    assert "one-shot" in result
    tasks = json.loads(tasks_file.read_text())
    assert tasks[0]["one_shot"] is True


# ── cron_list tool ───────────────────────────────────────────────────────────

def test_cron_list_empty(tmp_path):
    from agent.tools.cron import cron_list
    with patch("agent.tools.cron._TASKS_FILE", tmp_path / "tasks.json"):
        result = cron_list.invoke({})
    assert "No cron tasks" in result


def test_cron_list_shows_tasks(tmp_path):
    from agent.tools.cron import cron_create, cron_list
    tasks_file = tmp_path / "tasks.json"
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        cron_create.invoke({"schedule": "0 8 * * *", "description": "Daily standup"})
        result = cron_list.invoke({})
    assert "Daily standup" in result
    assert "0 8 * * *" in result


# ── cron_delete tool ─────────────────────────────────────────────────────────

def test_cron_delete_existing(tmp_path):
    from agent.tools.cron import cron_create, cron_delete, cron_list
    tasks_file = tmp_path / "tasks.json"
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        cron_create.invoke({"schedule": "* * * * *", "description": "to delete"})
        tasks = json.loads(tasks_file.read_text())
        task_id = tasks[0]["id"]
        result = cron_delete.invoke({"task_id": task_id})
    assert "deleted" in result


def test_cron_delete_nonexistent(tmp_path):
    from agent.tools.cron import cron_delete
    with patch("agent.tools.cron._TASKS_FILE", tmp_path / "tasks.json"):
        result = cron_delete.invoke({"task_id": "nonexistent"})
    assert "not found" in result


# ── run_pending_cron ─────────────────────────────────────────────────────────

def test_run_pending_cron_fires_due_task(tmp_path):
    from agent.tools.cron import run_pending_cron
    task = _make_task("* * * * *", description="fire me")
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([task]))
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        executed = run_pending_cron()
    assert "fire me" in executed


def test_run_pending_cron_no_fire_when_not_due(tmp_path):
    from agent.tools.cron import run_pending_cron
    last = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    task = _make_task("* * * * *", last_run=last)
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([task]))
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        executed = run_pending_cron()
    assert executed == []


def test_run_pending_cron_prunes_completed_one_shot(tmp_path):
    from agent.tools.cron import run_pending_cron
    last = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    task = _make_task("* * * * *", one_shot=True, last_run=last)
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([task]))
    with patch("agent.tools.cron._TASKS_FILE", tasks_file):
        run_pending_cron()
        remaining = json.loads(tasks_file.read_text())
    assert remaining == []
