"""
Tests for tui.py — TUI helper functions and widget basics.

Since textual is installed, we import the real tui module.
We test pure helper functions and constants only (no app launch).
"""
import pytest


# Import helpers directly — textual is installed
from tui import (
    _short_path, _tool_label, _fmt_elapsed, _get_model_name, _get_workspace,
    TOOL_ICONS, COMMANDS, AGENT_COLORS, VERSION,
)


# ── _short_path ──────────────────────────────────────────────

def test_short_path_empty():
    assert _short_path("") == ""


def test_short_path_short():
    assert _short_path("dir/file.py") == "dir/file.py"


def test_short_path_long():
    assert _short_path("/home/user/projects/myapp/src/main.py") == "src/main.py"


def test_short_path_backslashes():
    assert _short_path("C:\\Users\\dev\\project\\file.py") == "project/file.py"


# ── _tool_label ──────────────────────────────────────────────

def test_tool_label_file_read():
    label = _tool_label("file_read", {"file_path": "/home/user/project/src/main.py"})
    assert "Read" in label
    assert "src/main.py" in label


def test_tool_label_terminal_exec():
    label = _tool_label("terminal_exec", {"command": "python -m pytest tests/"})
    assert "$" in label
    assert "python" in label


def test_tool_label_grep_search():
    label = _tool_label("grep_search", {"pattern": "TODO"})
    assert "Grep" in label
    assert "TODO" in label


def test_tool_label_git_tool():
    label = _tool_label("git_status", {})
    assert "git status" in label


def test_tool_label_unknown_tool():
    label = _tool_label("custom_xyz", {})
    assert "custom_xyz" in label


def test_tool_label_file_edit():
    label = _tool_label("file_edit", {"file_path": "/project/src/app.py"})
    assert "Edit" in label
    assert "src/app.py" in label


def test_tool_label_code_search():
    label = _tool_label("code_search", {"query": "def main"})
    assert "Search" in label
    assert "def main" in label


def test_tool_label_task_explore():
    label = _tool_label("task_explore", {"task": "Find all API endpoints"})
    assert "Explore" in label
    assert "Find all" in label


# ── _fmt_elapsed ─────────────────────────────────────────────

def test_fmt_elapsed_milliseconds():
    assert _fmt_elapsed(0.5) == "500ms"


def test_fmt_elapsed_seconds():
    assert _fmt_elapsed(3.14) == "3.1s"


def test_fmt_elapsed_minutes():
    assert _fmt_elapsed(90.0) == "1.5m"


def test_fmt_elapsed_zero():
    assert _fmt_elapsed(0.0) == "0ms"


def test_fmt_elapsed_boundary():
    assert _fmt_elapsed(1.0) == "1.0s"
    assert _fmt_elapsed(60.0) == "1.0m"


# ── _clean_buffer (test regex logic) ─────────────────────────

import re

def _clean_buffer(buffer: str) -> str:
    """Replica of ShadowDevTUI._clean_buffer for standalone testing."""
    clean = buffer
    clean = re.sub(r'```json\s*\{\s*"name"[\s\S]*?(?:```|$)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\{\s*"name"\s*:\s*"[^"]+\"[\s\S]*?"arguments"\s*:[\s\S]*?(?:\}|$)', '', clean, flags=re.IGNORECASE)
    stripped = clean.strip()
    if stripped in ("{", "}", "{}", "{\n}", "{\n  \n}"):
        return ""
    return stripped


def test_clean_buffer_removes_json_tool_call():
    result = _clean_buffer('Hello world ```json\n{"name": "file_read", "arguments": {"path": "x"}}```')
    assert "Hello world" in result
    assert '"name"' not in result


def test_clean_buffer_empty_json():
    assert _clean_buffer("{}") == ""
    assert _clean_buffer("{\n  \n}") == ""


def test_clean_buffer_preserves_normal_text():
    assert _clean_buffer("This is normal output.") == "This is normal output."


def test_clean_buffer_strips_inline_tool_json():
    result = _clean_buffer('Before {"name": "grep_search", "arguments": {"query": "x"}} after')
    assert "Before" in result


# ── Constants ────────────────────────────────────────────────

def test_agent_colors_has_required():
    assert "planner" in AGENT_COLORS
    assert "coder" in AGENT_COLORS
    for agent, color in AGENT_COLORS.items():
        assert isinstance(color, str)


def test_tool_icons_has_common_tools():
    expected = ["file_read", "file_write", "terminal_exec", "grep_search", "git_status"]
    for tool in expected:
        assert tool in TOOL_ICONS, f"Missing {tool} in TOOL_ICONS"
        icon, label = TOOL_ICONS[tool]
        assert isinstance(icon, str)
        assert isinstance(label, str) and len(label) > 0


def test_tool_icons_count():
    assert len(TOOL_ICONS) >= 30, f"Expected 30+ tool icons, got {len(TOOL_ICONS)}"


def test_commands_have_slash_prefix():
    assert len(COMMANDS) >= 5
    for cmd, desc in COMMANDS:
        assert cmd.startswith("/"), f"Command {cmd} missing / prefix"
        assert isinstance(desc, str) and len(desc) > 0


def test_commands_include_essentials():
    cmd_names = [c[0] for c in COMMANDS]
    for essential in ["/plan", "/code", "/help", "/exit", "/clear"]:
        assert essential in cmd_names, f"Missing essential command {essential}"


# ── Metadata ─────────────────────────────────────────────────

def test_get_model_name():
    name = _get_model_name()
    assert isinstance(name, str) and len(name) > 0


def test_get_workspace():
    ws = _get_workspace()
    assert isinstance(ws, str) and len(ws) > 0


def test_version():
    assert VERSION == "3.0.0"
