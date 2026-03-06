"""Tests for agent/mcp_client.py."""
import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────

class _FakeMCPTool:
    def __init__(self, name, description="A tool", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {}


class _FakeListToolsResponse:
    def __init__(self, tools):
        self.tools = tools


class _FakeCallResult:
    def __init__(self, text, is_error=False):
        content_item = MagicMock()
        content_item.text = text
        self.content = [content_item]
        self.isError = is_error


# ── load_mcp_tools: trivial cases ─────────────────────────────

def test_load_mcp_tools_empty():
    """Empty MCP_SERVERS → ([], [])."""
    from agent.mcp_client import load_mcp_tools
    planner, coder = load_mcp_tools({})
    assert planner == []
    assert coder == []


def test_load_mcp_tools_no_mcp_package():
    """When mcp is not installed, returns ([], []) with a warning."""
    from agent import mcp_client as mc
    original = mc._HAS_MCP
    try:
        mc._HAS_MCP = False
        planner, coder = mc.load_mcp_tools({"server": {"type": "stdio", "command": "npx"}})
        assert planner == []
        assert coder == []
    finally:
        mc._HAS_MCP = original


# ── JSON Schema → Python type ──────────────────────────────────

def test_json_schema_to_python_type_basic():
    from agent.mcp_client import _json_schema_to_python_type
    assert _json_schema_to_python_type({"type": "string"}) is str
    assert _json_schema_to_python_type({"type": "integer"}) is int
    assert _json_schema_to_python_type({"type": "number"}) is float
    assert _json_schema_to_python_type({"type": "boolean"}) is bool
    assert _json_schema_to_python_type({"type": "array"}) is list
    assert _json_schema_to_python_type({"type": "object"}) is dict


def test_json_schema_to_python_type_unknown_falls_back():
    from agent.mcp_client import _json_schema_to_python_type
    result = _json_schema_to_python_type({"type": "null"})
    assert result is Any


# ── build_args_schema ─────────────────────────────────────────

def test_build_args_schema_none_input():
    from agent.mcp_client import _build_args_schema
    assert _build_args_schema("Model", None) is None


def test_build_args_schema_empty_properties():
    from agent.mcp_client import _build_args_schema
    assert _build_args_schema("Model", {"properties": {}}) is None


def test_build_args_schema_creates_model():
    from agent.mcp_client import _build_args_schema
    schema = {
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "count": {"type": "integer"},
        },
        "required": ["path"],
    }
    Model = _build_args_schema("MyModel", schema)
    assert Model is not None
    m = Model(path="/tmp/file", count=5)
    assert m.path == "/tmp/file"
    assert m.count == 5
    # optional field defaults to None
    m2 = Model(path="/tmp/other")
    assert m2.count is None


# ── _extract_text ──────────────────────────────────────────────

def test_extract_text_normal():
    from agent.mcp_client import _extract_text
    item = MagicMock()
    item.text = "hello world"
    result = MagicMock()
    result.content = [item]
    result.isError = False
    assert _extract_text(result) == "hello world"


def test_extract_text_is_error():
    from agent.mcp_client import _extract_text
    item = MagicMock()
    item.text = "something went wrong"
    result = MagicMock()
    result.content = [item]
    result.isError = True
    out = _extract_text(result)
    assert "[MCP tool returned error]" in out
    assert "something went wrong" in out


def test_extract_text_no_content():
    from agent.mcp_client import _extract_text
    result = MagicMock()
    result.content = []
    result.isError = False
    assert _extract_text(result) == "(no output)"


# ── access routing ─────────────────────────────────────────────

def test_access_write_goes_to_coder_only():
    """Tools from a server with access='write' go to coder_only bucket."""
    from agent import mcp_client as mc

    async def _fake_discover(server_name, cfg):
        lc_tool = MagicMock()
        lc_tool.name = f"mcp_{server_name}_do_thing"
        return [lc_tool]

    async def _run():
        with patch.object(mc, "_discover_server_tools", side_effect=_fake_discover):
            return await mc._load_all_mcp_tools_async(
                {"myserver": {"type": "stdio", "command": "x", "access": "write"}}
            )

    planner, coder = asyncio.run(_run())
    assert len(planner) == 0
    assert len(coder) == 1


def test_access_read_goes_to_planner():
    """Tools from a server with access='read' go to planner bucket."""
    from agent import mcp_client as mc

    async def _fake_discover(server_name, cfg):
        lc_tool = MagicMock()
        lc_tool.name = f"mcp_{server_name}_read_tool"
        return [lc_tool]

    async def _run():
        with patch.object(mc, "_discover_server_tools", side_effect=_fake_discover):
            return await mc._load_all_mcp_tools_async(
                {"myserver": {"type": "stdio", "command": "x", "access": "read"}}
            )

    planner, coder = asyncio.run(_run())
    assert len(planner) == 1
    assert len(coder) == 0


def test_access_both_goes_to_planner():
    """Tools from a server with access='both' (default) go to planner bucket."""
    from agent import mcp_client as mc

    async def _fake_discover(server_name, cfg):
        lc_tool = MagicMock()
        lc_tool.name = f"mcp_{server_name}_shared_tool"
        return [lc_tool]

    async def _run():
        with patch.object(mc, "_discover_server_tools", side_effect=_fake_discover):
            return await mc._load_all_mcp_tools_async(
                {"myserver": {"type": "stdio", "command": "x"}}  # no access = "both"
            )

    planner, coder = asyncio.run(_run())
    assert len(planner) == 1
    assert len(coder) == 0


# ── connection failure ─────────────────────────────────────────

def test_server_connection_failure_returns_empty():
    """A server that fails to connect returns empty lists, not an exception."""
    from agent import mcp_client as mc

    with patch("agent.mcp_client._run_in_thread", side_effect=RuntimeError("conn fail")):
        planner, coder = mc.load_mcp_tools({"bad": {"type": "stdio", "command": "bad"}})

    assert planner == []
    assert coder == []


# ── _discover_server_tools: missing fields ─────────────────────

def test_discover_stdio_missing_command():
    """stdio server with no 'command' → empty list."""
    from agent import mcp_client as mc
    with patch.object(mc, "_HAS_MCP", True):
        tools = asyncio.run(mc._discover_server_tools("s", {"type": "stdio"}))
    assert tools == []


def test_discover_sse_no_mcp_package():
    """SSE server when _HAS_SSE is False → empty list."""
    from agent import mcp_client as mc
    with patch.object(mc, "_HAS_MCP", True):
        with patch.object(mc, "_HAS_SSE", False):
            tools = asyncio.run(
                mc._discover_server_tools("s", {"type": "sse", "url": "http://x"})
            )
    assert tools == []


def test_discover_sse_missing_url():
    """SSE server with no 'url' → empty list."""
    from agent import mcp_client as mc
    with patch.object(mc, "_HAS_MCP", True):
        with patch.object(mc, "_HAS_SSE", True):
            tools = asyncio.run(mc._discover_server_tools("s", {"type": "sse"}))
    assert tools == []


def test_discover_unknown_type():
    """Unknown server type → empty list."""
    from agent import mcp_client as mc
    tools = asyncio.run(mc._discover_server_tools("s", {"type": "grpc"}))
    assert tools == []
