"""Tests for the tool search and tool list tools."""
import pytest
from unittest.mock import MagicMock


def _make_mock_tool(name: str, description: str):
    t = MagicMock()
    t.name = name
    t.description = description
    return t


def _populate_registry(tools: list):
    from agent.tools.tool_search import register_tools, _TOOL_REGISTRY
    _TOOL_REGISTRY.clear()
    register_tools(tools)


# ── register_tools ───────────────────────────────────────────────────────────

def test_register_tools_populates_registry():
    from agent.tools.tool_search import _TOOL_REGISTRY
    tools = [_make_mock_tool("file_read", "Read a file from disk.")]
    _populate_registry(tools)
    assert "file_read" in _TOOL_REGISTRY


def test_register_tools_uses_first_line_of_description():
    from agent.tools.tool_search import _TOOL_REGISTRY
    tools = [_make_mock_tool("multi_line", "First line.\nSecond line.\nThird.")]
    _populate_registry(tools)
    desc, _ = _TOOL_REGISTRY["multi_line"]
    assert "First line." in desc
    assert "Second line." not in desc


def test_register_tools_clears_previous_registry():
    from agent.tools.tool_search import _TOOL_REGISTRY
    _populate_registry([_make_mock_tool("old_tool", "old desc")])
    _populate_registry([_make_mock_tool("new_tool", "new desc")])
    assert "old_tool" not in _TOOL_REGISTRY
    assert "new_tool" in _TOOL_REGISTRY


def test_register_tools_handles_none_description():
    from agent.tools.tool_search import _TOOL_REGISTRY
    t = _make_mock_tool("no_desc", None)
    t.description = None
    _populate_registry([t])
    assert "no_desc" in _TOOL_REGISTRY


# ── tool_search ──────────────────────────────────────────────────────────────

def test_tool_search_finds_by_name_keyword():
    from agent.tools.tool_search import tool_search
    tools = [
        _make_mock_tool("file_read", "Read a file."),
        _make_mock_tool("git_commit", "Commit changes to git."),
    ]
    _populate_registry(tools)
    result = tool_search.invoke({"query": "file"})
    assert "file_read" in result


def test_tool_search_finds_by_description_keyword():
    from agent.tools.tool_search import tool_search
    tools = [
        _make_mock_tool("run_tests", "Execute the test suite using pytest."),
        _make_mock_tool("file_write", "Write content to disk."),
    ]
    _populate_registry(tools)
    result = tool_search.invoke({"query": "pytest"})
    assert "run_tests" in result


def test_tool_search_no_match_returns_no_match():
    from agent.tools.tool_search import tool_search
    _populate_registry([_make_mock_tool("file_read", "Read a file.")])
    result = tool_search.invoke({"query": "zxqwerty999"})
    assert "No tools found" in result or "matches" in result.lower()


def test_tool_search_empty_registry_returns_hint():
    from agent.tools.tool_search import tool_search, _TOOL_REGISTRY
    _TOOL_REGISTRY.clear()
    result = tool_search.invoke({"query": "anything"})
    assert "empty" in result.lower() or "registry" in result.lower()


def test_tool_search_respects_limit():
    from agent.tools.tool_search import tool_search
    tools = [_make_mock_tool(f"tool_{i}", f"description with file keyword") for i in range(20)]
    _populate_registry(tools)
    result = tool_search.invoke({"query": "file", "limit": 3})
    # Should mention at most 3 + "and N more"
    lines = [l for l in result.split("\n") if l.strip().startswith("tool_")]
    assert len(lines) <= 3


def test_tool_search_name_match_scored_higher():
    from agent.tools.tool_search import tool_search
    tools = [
        _make_mock_tool("git_commit", "Commit staged changes."),
        _make_mock_tool("run_tests", "Commit hooks run tests."),
    ]
    _populate_registry(tools)
    result = tool_search.invoke({"query": "git commit"})
    # git_commit should appear before run_tests
    pos_git = result.find("git_commit")
    pos_run = result.find("run_tests")
    assert pos_git < pos_run or pos_run == -1


# ── tool_list ────────────────────────────────────────────────────────────────

def test_tool_list_shows_all_tools():
    from agent.tools.tool_search import tool_list
    tools = [
        _make_mock_tool("file_read", "Read files."),
        _make_mock_tool("git_log", "Show git history."),
        _make_mock_tool("memory_save", "Save a memory."),
    ]
    _populate_registry(tools)
    result = tool_list.invoke({})
    assert "file_read" in result
    assert "git_log" in result
    assert "memory_save" in result


def test_tool_list_groups_by_category():
    from agent.tools.tool_search import tool_list
    tools = [
        _make_mock_tool("file_read", "Read files."),
        _make_mock_tool("file_write", "Write files."),
        _make_mock_tool("git_commit", "Commit."),
    ]
    _populate_registry(tools)
    result = tool_list.invoke({})
    assert "File Operations" in result
    assert "Git" in result


def test_tool_list_empty_registry():
    from agent.tools.tool_search import tool_list, _TOOL_REGISTRY
    _TOOL_REGISTRY.clear()
    result = tool_list.invoke({})
    assert "empty" in result.lower()


def test_tool_list_shows_total_count():
    from agent.tools.tool_search import tool_list
    tools = [_make_mock_tool(f"tool_{i}", "desc") for i in range(5)]
    _populate_registry(tools)
    result = tool_list.invoke({})
    assert "5" in result
