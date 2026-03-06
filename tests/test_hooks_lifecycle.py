"""Tests for lifecycle hook events added in 3B-1."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from agent.hooks import (
    HookResult, LifecycleHook,
    PRE_TOOL_HOOKS, POST_TOOL_HOOKS, LIFECYCLE_HOOKS,
    register_hook, clear_hooks,
    run_lifecycle_hook,
    load_hooks_from_config,
    _LIFECYCLE_EVENTS, _TOOL_EVENTS,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_hooks()
    yield
    clear_hooks()


# ── register_hook: lifecycle events ───────────────────────────

def test_register_lifecycle_hook_session_start():
    register_hook("session_start", handler=lambda e, d: None)
    assert len(LIFECYCLE_HOOKS) == 1
    assert len(PRE_TOOL_HOOKS) == 0


def test_register_lifecycle_hook_stop():
    register_hook("stop", command="echo done")
    assert len(LIFECYCLE_HOOKS) == 1
    assert LIFECYCLE_HOOKS[0].event == "stop"


def test_register_all_lifecycle_events():
    for event in _LIFECYCLE_EVENTS:
        register_hook(event, handler=lambda e, d: None)
    assert len(LIFECYCLE_HOOKS) == len(_LIFECYCLE_EVENTS)


def test_register_invalid_event_raises():
    with pytest.raises(ValueError, match="must be one of"):
        register_hook("on_fire", handler=lambda: None)


def test_register_hook_no_handler_raises():
    with pytest.raises(ValueError):
        register_hook("session_start")


def test_register_tool_hook_still_works():
    """Existing tool hook registration is unaffected."""
    register_hook("pre_tool_use", pattern="file_*", handler=lambda n, a: HookResult())
    assert len(PRE_TOOL_HOOKS) == 1
    assert len(LIFECYCLE_HOOKS) == 0


# ── clear_hooks clears lifecycle too ──────────────────────────

def test_clear_hooks_clears_lifecycle():
    register_hook("session_start", handler=lambda e, d: None)
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    clear_hooks()
    assert len(LIFECYCLE_HOOKS) == 0
    assert len(PRE_TOOL_HOOKS) == 0


# ── run_lifecycle_hook: Python handler ─────────────────────────

def test_run_lifecycle_hook_no_hooks():
    """No lifecycle hooks → returns None quickly."""
    result = asyncio.run(run_lifecycle_hook("session_start"))
    assert result is None


def test_run_lifecycle_hook_python_handler_called():
    called = []

    def handler(event, data):
        called.append((event, data))

    register_hook("session_start", handler=handler)
    asyncio.run(run_lifecycle_hook("session_start", {"thread_id": "t1"}))
    assert len(called) == 1
    assert called[0][0] == "session_start"
    assert called[0][1]["thread_id"] == "t1"


def test_run_lifecycle_hook_async_handler():
    called = []

    async def async_handler(event, data):
        called.append(event)

    register_hook("stop", handler=async_handler)
    asyncio.run(run_lifecycle_hook("stop", {}))
    assert "stop" in called


def test_run_lifecycle_hook_wrong_event_not_called():
    """Hook for 'stop' should not fire on 'session_start'."""
    called = []
    register_hook("stop", handler=lambda e, d: called.append(e))
    asyncio.run(run_lifecycle_hook("session_start", {}))
    assert called == []


def test_run_lifecycle_hook_handler_exception_is_swallowed():
    """Exceptions in lifecycle hooks should not propagate."""
    def bad_handler(event, data):
        raise RuntimeError("hook exploded")

    register_hook("session_end", handler=bad_handler)
    # Should not raise
    result = asyncio.run(run_lifecycle_hook("session_end", {}))
    assert result is None


# ── user_prompt_submit: can modify prompt ─────────────────────

def test_user_prompt_submit_modified_prompt_python():
    def rewrite(event, data):
        return {"modified_prompt": "REWRITTEN: " + data.get("prompt", "")}

    register_hook("user_prompt_submit", handler=rewrite)
    result = asyncio.run(run_lifecycle_hook("user_prompt_submit", {"prompt": "hello"}))
    assert result == "REWRITTEN: hello"


def test_user_prompt_submit_no_modification_returns_none():
    register_hook("user_prompt_submit", handler=lambda e, d: None)
    result = asyncio.run(run_lifecycle_hook("user_prompt_submit", {"prompt": "hello"}))
    assert result is None


def test_user_prompt_submit_last_hook_wins():
    """When multiple hooks modify the prompt, last one wins."""
    register_hook("user_prompt_submit", handler=lambda e, d: {"modified_prompt": "first"})
    register_hook("user_prompt_submit", handler=lambda e, d: {"modified_prompt": "second"})
    result = asyncio.run(run_lifecycle_hook("user_prompt_submit", {"prompt": "x"}))
    assert result == "second"


# ── load_hooks_from_config: lifecycle support ──────────────────

def test_load_hooks_from_config_lifecycle():
    """Lifecycle hooks in config are registered correctly."""
    config = [
        {"event": "session_start", "command": "notify.sh", "name": "start_notify"},
        {"event": "stop", "command": "log.sh"},
    ]
    load_hooks_from_config(config)
    assert len(LIFECYCLE_HOOKS) == 2
    assert LIFECYCLE_HOOKS[0].event == "session_start"
    assert LIFECYCLE_HOOKS[0].command == "notify.sh"


def test_load_hooks_from_config_mixed():
    """Mix of tool and lifecycle hooks in same config."""
    config = [
        {"event": "pre_tool_use", "pattern": "*", "command": "check.sh"},
        {"event": "session_end", "command": "cleanup.sh"},
    ]
    load_hooks_from_config(config)
    assert len(PRE_TOOL_HOOKS) == 1
    assert len(LIFECYCLE_HOOKS) == 1


def test_load_hooks_from_config_invalid_lifecycle_event():
    """Unknown lifecycle event is skipped with warning."""
    config_data = [{"event": "on_fire", "command": "burn.sh"}]
    load_hooks_from_config(config_data)
    assert len(LIFECYCLE_HOOKS) == 0
    assert len(PRE_TOOL_HOOKS) == 0


def test_load_hooks_from_config_missing_command():
    """Hook with no command is skipped."""
    config_data = [{"event": "session_start", "name": "no_cmd"}]
    load_hooks_from_config(config_data)
    assert len(LIFECYCLE_HOOKS) == 0


# ── LifecycleHook dataclass ────────────────────────────────────

def test_lifecycle_hook_fields():
    hook = LifecycleHook(event="session_start", command="run.sh", name="my_hook")
    assert hook.event == "session_start"
    assert hook.command == "run.sh"
    assert hook.name == "my_hook"
    assert hook.handler is None
