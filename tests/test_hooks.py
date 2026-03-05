"""Tests for agent/hooks.py — hook system."""
import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock

from agent.hooks import (
    HookResult, ToolHook,
    PRE_TOOL_HOOKS, POST_TOOL_HOOKS,
    register_hook, clear_hooks,
    run_pre_hooks, run_post_hooks,
    load_hooks_from_config, load_hooks_from_file,
)


@pytest.fixture(autouse=True)
def _clear():
    """Clear hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


# ── ToolHook.matches ──────────────────────────────────────

def test_hook_matches_exact():
    h = ToolHook(event="pre_tool_use", pattern="terminal_exec")
    assert h.matches("terminal_exec")
    assert not h.matches("file_read")


def test_hook_matches_glob():
    h = ToolHook(event="pre_tool_use", pattern="file_*")
    assert h.matches("file_read")
    assert h.matches("file_write")
    assert not h.matches("terminal_exec")


def test_hook_matches_wildcard():
    h = ToolHook(event="pre_tool_use", pattern="*")
    assert h.matches("anything")
    assert h.matches("file_read")


# ── register_hook ─────────────────────────────────────────

def test_register_pre_hook():
    register_hook("pre_tool_use", "file_*", handler=lambda *a: HookResult())
    assert len(PRE_TOOL_HOOKS) == 1
    assert len(POST_TOOL_HOOKS) == 0


def test_register_post_hook():
    register_hook("post_tool_use", "*", handler=lambda *a: HookResult())
    assert len(POST_TOOL_HOOKS) == 1


def test_register_invalid_event():
    with pytest.raises(ValueError, match="event must be"):
        register_hook("invalid", "*", handler=lambda *a: HookResult())


def test_register_no_handler_no_command():
    with pytest.raises(ValueError, match="Must provide"):
        register_hook("pre_tool_use", "*")


# ── run_pre_hooks ─────────────────────────────────────────

def test_pre_hook_passthrough():
    """Hook that doesn't block — tool should proceed."""
    register_hook("pre_tool_use", "*", handler=lambda name, args: HookResult())
    result = asyncio.run(run_pre_hooks("file_read", {"file_path": "test.py"}))
    assert not result.block


def test_pre_hook_blocks():
    """Hook that blocks tool execution."""
    def blocker(name, args):
        return HookResult(block=True, reason="Dangerous command")

    register_hook("pre_tool_use", "terminal_exec", handler=blocker)
    result = asyncio.run(run_pre_hooks("terminal_exec", {"command": "rm -rf /"}))
    assert result.block
    assert "Dangerous" in result.reason


def test_pre_hook_no_match():
    """Hook doesn't match tool name — no effect."""
    def blocker(name, args):
        return HookResult(block=True, reason="Blocked")

    register_hook("pre_tool_use", "terminal_exec", handler=blocker)
    result = asyncio.run(run_pre_hooks("file_read", {"file_path": "test.py"}))
    assert not result.block


def test_pre_hook_modifies_args():
    """Hook modifies tool args."""
    def modifier(name, args):
        return HookResult(modified_args={**args, "injected": True})

    register_hook("pre_tool_use", "*", handler=modifier)
    result = asyncio.run(run_pre_hooks("file_read", {"file_path": "test.py"}))
    assert result.modified_args["injected"] is True


def test_pre_hook_async_handler():
    """Async Python hook handler."""
    async def async_hook(name, args):
        return HookResult(inject_message="Checked by async hook")

    register_hook("pre_tool_use", "*", handler=async_hook)
    result = asyncio.run(run_pre_hooks("file_read", {}))
    assert result.inject_message == "Checked by async hook"


# ── run_post_hooks ────────────────────────────────────────

def test_post_hook_modifies_output():
    """Post-hook modifies tool output."""
    def redactor(name, args, output):
        return HookResult(modified_output=output.replace("SECRET", "***"))

    register_hook("post_tool_use", "*", handler=redactor)
    result = asyncio.run(run_post_hooks("file_read", {}, "Content: SECRET value"))
    assert result.modified_output == "Content: *** value"


# ── load_hooks_from_config ────────────────────────────────

def test_load_hooks_from_config():
    config = [
        {"event": "pre_tool_use", "pattern": "terminal_exec", "command": "echo test"},
        {"event": "post_tool_use", "pattern": "*", "command": "echo log"},
    ]
    load_hooks_from_config(config)
    assert len(PRE_TOOL_HOOKS) == 1
    assert len(POST_TOOL_HOOKS) == 1


def test_load_hooks_skips_invalid():
    config = [
        {"event": "invalid", "pattern": "*", "command": "echo test"},
        {"event": "pre_tool_use", "pattern": "*"},  # no command
    ]
    load_hooks_from_config(config)
    assert len(PRE_TOOL_HOOKS) == 0
    assert len(POST_TOOL_HOOKS) == 0


# ── load_hooks_from_file ──────────────────────────────────

def test_load_hooks_from_file(tmp_path):
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps([
        {"event": "pre_tool_use", "pattern": "*", "command": "echo ok"}
    ]))
    load_hooks_from_file(str(hooks_file))
    assert len(PRE_TOOL_HOOKS) == 1


def test_load_hooks_from_nonexistent():
    load_hooks_from_file("/nonexistent/hooks.json")
    assert len(PRE_TOOL_HOOKS) == 0
    assert len(POST_TOOL_HOOKS) == 0


def test_load_hooks_from_empty_path():
    load_hooks_from_file("")
    assert len(PRE_TOOL_HOOKS) == 0
