"""Tests for agent/plugin_registry.py (3D-1 Plugin Registry)."""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────

def make_tool(name: str) -> MagicMock:
    """Create a minimal mock LangChain tool."""
    t = MagicMock()
    t.name = name
    t.invoke = MagicMock(return_value="ok")
    return t


def make_plugin_module(tools, access="read", name="test_plugin",
                       version="", author="", description=""):
    """Create a fake plugin module."""
    mod = types.ModuleType(f"_test_plugin_{name}")
    mod.__skill_tools__ = tools
    mod.__skill_access__ = access
    mod.__skill_name__ = name
    mod.__skill_version__ = version
    mod.__skill_author__ = author
    mod.__skill_description__ = description
    return mod


def make_entry_point(ep_name: str, module) -> MagicMock:
    """Create a mock importlib entry_point."""
    ep = MagicMock()
    ep.name = ep_name
    ep.load = MagicMock(return_value=module)
    return ep


# ── _extract_plugin_info ──────────────────────────────────────

def test_extract_missing_skill_tools():
    """Module without __skill_tools__ gives error PluginInfo."""
    from agent.plugin_registry import _extract_plugin_info
    mod = types.ModuleType("empty_plugin")
    info = _extract_plugin_info(mod, name="empty")
    assert info.error == "missing __skill_tools__"
    assert info.tools == []


def test_extract_wrong_type_for_tools():
    """Non-list __skill_tools__ gives error."""
    from agent.plugin_registry import _extract_plugin_info
    mod = types.ModuleType("bad_plugin")
    mod.__skill_tools__ = "not_a_list"
    info = _extract_plugin_info(mod, name="bad")
    assert "must be a list" in info.error


def test_extract_valid_tools():
    """Valid module extracts tools and metadata correctly."""
    from agent.plugin_registry import _extract_plugin_info
    t1 = make_tool("db_query")
    t2 = make_tool("db_insert")
    mod = make_plugin_module(
        tools=[t1, t2], access="write",
        name="Database", version="2.0", author="Alice",
        description="Database tools",
    )
    info = _extract_plugin_info(mod, name="database")
    assert not info.error
    assert len(info.tools) == 2
    assert info.access == "write"
    assert info.version == "2.0"
    assert info.author == "Alice"
    assert info.description == "Database tools"


def test_extract_invalid_access_defaults_to_read():
    """Unknown __skill_access__ defaults to 'read'."""
    from agent.plugin_registry import _extract_plugin_info
    mod = make_plugin_module(tools=[make_tool("t1")], access="superuser")
    info = _extract_plugin_info(mod, name="p")
    assert info.access == "read"
    assert not info.error


def test_extract_tool_without_invoke_is_skipped():
    """Tools missing .invoke attribute are filtered out."""
    from agent.plugin_registry import _extract_plugin_info
    bad_tool = MagicMock(spec=[])  # no invoke
    bad_tool.name = "bad"
    good_tool = make_tool("good")
    mod = make_plugin_module(tools=[bad_tool, good_tool])
    info = _extract_plugin_info(mod, name="p")
    assert len(info.tools) == 1
    assert info.tools[0].name == "good"


# ── discover_plugins ──────────────────────────────────────────

def test_discover_plugins_empty():
    """No installed plugins returns empty list."""
    from agent.plugin_registry import discover_plugins
    with patch("importlib.metadata.entry_points", return_value=[]):
        result = discover_plugins()
    assert result == []


def test_discover_plugins_finds_entry_points():
    """Entry points are loaded and returned as PluginInfo."""
    from agent.plugin_registry import discover_plugins
    t = make_tool("my_tool")
    mod = make_plugin_module(tools=[t], name="my_plugin")
    eps = [make_entry_point("my_plugin", mod)]

    with patch("importlib.metadata.entry_points", return_value=eps):
        result = discover_plugins()

    assert len(result) == 1
    assert result[0].name == "my_plugin"
    assert len(result[0].tools) == 1
    assert not result[0].error


def test_discover_plugins_failed_load_returns_error_info():
    """If entry_point.load() raises, PluginInfo has .error set."""
    from agent.plugin_registry import discover_plugins
    ep = MagicMock()
    ep.name = "broken_plugin"
    ep.load = MagicMock(side_effect=ImportError("missing dep"))

    with patch("importlib.metadata.entry_points", return_value=[ep]):
        result = discover_plugins()

    assert len(result) == 1
    assert result[0].name == "broken_plugin"
    assert "ImportError" in result[0].error


def test_discover_plugins_importlib_failure_returns_empty():
    """If entry_points() itself raises, returns empty list (no crash)."""
    from agent.plugin_registry import discover_plugins
    with patch("importlib.metadata.entry_points", side_effect=Exception("discovery failed")):
        result = discover_plugins()
    assert result == []


# ── get_plugin_tools ──────────────────────────────────────────

def test_get_plugin_tools_empty():
    """No plugins → returns two empty lists."""
    from agent.plugin_registry import get_plugin_tools
    with patch("agent.plugin_registry.discover_plugins", return_value=[]):
        planner, coder = get_plugin_tools()
    assert planner == []
    assert coder == []


def test_get_plugin_tools_read_access_goes_to_planner():
    """Tools with access='read' appear in planner_tools."""
    from agent.plugin_registry import get_plugin_tools, PluginInfo
    t = make_tool("read_tool")
    plugin = PluginInfo(name="p", access="read", tools=[t])

    with patch("agent.plugin_registry.discover_plugins", return_value=[plugin]):
        planner, coder = get_plugin_tools()

    assert len(planner) == 1
    assert coder == []
    assert planner[0].name == "read_tool"


def test_get_plugin_tools_write_access_goes_to_coder_only():
    """Tools with access='write' appear only in coder_only_tools."""
    from agent.plugin_registry import get_plugin_tools, PluginInfo
    t = make_tool("write_tool")
    plugin = PluginInfo(name="p", access="write", tools=[t])

    with patch("agent.plugin_registry.discover_plugins", return_value=[plugin]):
        planner, coder = get_plugin_tools()

    assert planner == []
    assert len(coder) == 1
    assert coder[0].name == "write_tool"


def test_get_plugin_tools_deduplicates_against_existing():
    """Tools whose names conflict with existing_names are skipped."""
    from agent.plugin_registry import get_plugin_tools, PluginInfo
    t = make_tool("file_read")  # same as a core tool
    plugin = PluginInfo(name="p", access="read", tools=[t])

    with patch("agent.plugin_registry.discover_plugins", return_value=[plugin]):
        planner, coder = get_plugin_tools(existing_names={"file_read"})

    assert planner == []


def test_get_plugin_tools_deduplicates_within_plugins():
    """Two plugins with the same tool name — second one is skipped."""
    from agent.plugin_registry import get_plugin_tools, PluginInfo
    t1 = make_tool("dupe_tool")
    t2 = make_tool("dupe_tool")
    p1 = PluginInfo(name="p1", access="read", tools=[t1])
    p2 = PluginInfo(name="p2", access="read", tools=[t2])

    with patch("agent.plugin_registry.discover_plugins", return_value=[p1, p2]):
        planner, _ = get_plugin_tools()

    assert len(planner) == 1


def test_get_plugin_tools_error_plugins_are_skipped():
    """Plugins with .error set are skipped entirely."""
    from agent.plugin_registry import get_plugin_tools, PluginInfo
    bad = PluginInfo(name="bad", error="missing __skill_tools__")
    good = PluginInfo(name="good", access="read", tools=[make_tool("g_tool")])

    with patch("agent.plugin_registry.discover_plugins", return_value=[bad, good]):
        planner, _ = get_plugin_tools()

    assert len(planner) == 1
    assert planner[0].name == "g_tool"


# ── list_plugins ──────────────────────────────────────────────

def test_list_plugins_empty():
    """No plugins → empty list."""
    from agent.plugin_registry import list_plugins
    with patch("agent.plugin_registry.discover_plugins", return_value=[]):
        result = list_plugins()
    assert result == []


def test_list_plugins_includes_metadata():
    """list_plugins returns dicts with expected fields."""
    from agent.plugin_registry import list_plugins, PluginInfo
    t = make_tool("my_tool")
    plugin = PluginInfo(
        name="my_plugin", version="1.2.3", author="Bob",
        description="Does stuff", access="read", tools=[t],
    )
    with patch("agent.plugin_registry.discover_plugins", return_value=[plugin]):
        result = list_plugins()

    assert len(result) == 1
    info = result[0]
    assert info["name"] == "my_plugin"
    assert info["version"] == "1.2.3"
    assert info["author"] == "Bob"
    assert info["description"] == "Does stuff"
    assert info["status"] == "loaded"
    assert "my_tool" in info["tools"]
    assert info["tool_count"] == 1


def test_list_plugins_error_status():
    """Failed plugins show status='error' with error message."""
    from agent.plugin_registry import list_plugins, PluginInfo
    bad = PluginInfo(name="bad_plugin", error="ImportError: missing dep")

    with patch("agent.plugin_registry.discover_plugins", return_value=[bad]):
        result = list_plugins()

    assert result[0]["status"] == "error"
    assert "ImportError" in result[0]["error"]


# ── Integration: skill_list shows plugins ──────────────────────

def test_skill_list_shows_installed_plugins(tmp_path):
    """skill_list tool output includes installed pip plugins section."""
    from agent.plugin_registry import PluginInfo

    t = make_tool("ext_tool")
    plugin = PluginInfo(
        name="my-ext", version="0.1", author="Eve",
        description="External extension", access="read", tools=[t],
    )

    with patch("agent.plugin_registry.discover_plugins", return_value=[plugin]):
        with patch("agent.skill_engine.SKILLS_DIR", tmp_path):
            from agent.tools.skills import skill_list
            result = skill_list.invoke({})

    assert "Installed Plugins" in result
    assert "my-ext" in result


def test_skill_list_no_plugins_section_when_empty(tmp_path):
    """If no plugins installed, the Installed Plugins section is omitted."""
    with patch("agent.plugin_registry.discover_plugins", return_value=[]):
        with patch("agent.skill_engine.SKILLS_DIR", tmp_path):
            from agent.tools.skills import skill_list
            result = skill_list.invoke({})

    assert "Installed Plugins" not in result
