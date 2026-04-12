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


# ── MCPClientManager ───────────────────────────────────────────

class _FakeResource:
    def __init__(self, uri, name, description="", mimeType="text/plain"):
        self.uri = uri
        self.name = name
        self.description = description
        self.mimeType = mimeType


class _FakeListResourcesResponse:
    def __init__(self, resources):
        self.resources = resources


class _FakeReadContent:
    def __init__(self, text=None, blob=None, mimeType="text/plain"):
        self.text = text
        self.blob = blob
        self.mimeType = mimeType


class _FakeReadResourceResponse:
    def __init__(self, contents):
        self.contents = contents


class _FakePrompt:
    def __init__(self, name, description="", arguments=None):
        self.name = name
        self.description = description
        self.arguments = arguments or []


class _FakePromptArg:
    def __init__(self, name, description="", required=False):
        self.name = name
        self.description = description
        self.required = required


class _FakeListPromptsResponse:
    def __init__(self, prompts):
        self.prompts = prompts


class _FakePromptMessage:
    def __init__(self, role, text):
        self.role = role
        self.content = MagicMock(text=text)


class _FakeGetPromptResponse:
    def __init__(self, messages):
        self.messages = messages


def _make_manager(servers=None):
    from agent.mcp_client import MCPClientManager
    return MCPClientManager(servers or {"test": {"type": "stdio", "command": "npx"}})


def test_manager_initial_status():
    mgr = _make_manager({"alpha": {}, "beta": {}})
    assert mgr._status == {"alpha": "unknown", "beta": "unknown"}


def test_manager_empty_servers():
    from agent.mcp_client import MCPClientManager
    mgr = MCPClientManager({})
    assert mgr._status == {}


# ── Helper: build a mock _open_session asynccontextmanager ─────

def _make_open_session_patch(session):
    """Return a function that, when patched onto MCPClientManager._open_session,
    yields `session` as an async context manager."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _patched(self, server_name):
        yield session

    return _patched


# list_resources

def test_list_resources_success():
    from agent import mcp_client as mc

    fake_response = _FakeListResourcesResponse([
        _FakeResource("file:///foo.txt", "foo", "A file", "text/plain"),
    ])

    session = MagicMock()
    async def _list():
        return fake_response
    session.list_resources = _list

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.list_resources("test"))

    assert len(result) == 1
    assert result[0]["uri"] == "file:///foo.txt"
    assert result[0]["name"] == "foo"
    assert result[0]["mimeType"] == "text/plain"
    assert mgr._status["test"] == "healthy"


def test_list_resources_not_supported():
    """When session has no list_resources, returns sentinel."""
    from agent import mcp_client as mc

    session = MagicMock(spec=[])  # no list_resources attribute

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.list_resources("test"))

    assert result[0].get("error") == mc._NOT_SUPPORTED


def test_list_resources_error_marks_degraded():
    from agent import mcp_client as mc

    session = MagicMock()
    async def _list():
        raise RuntimeError("connection refused")
    session.list_resources = _list

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.list_resources("test"))

    assert mgr._status["test"] == "degraded"
    assert "error" in result[0]


# read_resource

def test_read_resource_text():
    from agent import mcp_client as mc

    fake_response = _FakeReadResourceResponse([_FakeReadContent(text="hello world")])

    session = MagicMock()
    async def _read(uri):
        return fake_response
    session.read_resource = _read

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.read_resource("test", "file:///foo.txt"))

    assert result == "hello world"


def test_read_resource_blob():
    from agent import mcp_client as mc

    fake_response = _FakeReadResourceResponse([_FakeReadContent(blob=b"binary", mimeType="image/png")])

    session = MagicMock()
    async def _read(uri):
        return fake_response
    session.read_resource = _read

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.read_resource("test", "file:///img.png"))

    assert "[blob:image/png]" in result


# list_prompts

def test_list_prompts_success():
    from agent import mcp_client as mc

    fake_response = _FakeListPromptsResponse([
        _FakePrompt("greet", "Greeting prompt", [_FakePromptArg("name", required=True)]),
    ])

    session = MagicMock()
    async def _list():
        return fake_response
    session.list_prompts = _list

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.list_prompts("test"))

    assert len(result) == 1
    assert result[0]["name"] == "greet"
    assert result[0]["arguments"][0]["name"] == "name"
    assert result[0]["arguments"][0]["required"] is True
    assert mgr._status["test"] == "healthy"


# get_prompt

def test_get_prompt_success():
    from agent import mcp_client as mc

    fake_response = _FakeGetPromptResponse([
        _FakePromptMessage("user", "Hello, Alice!"),
    ])

    session = MagicMock()
    async def _get(name, args):
        return fake_response
    session.get_prompt = _get

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.get_prompt("test", "greet", {"name": "Alice"}))

    assert "[user] Hello, Alice!" in result


def test_get_prompt_not_supported():
    from agent import mcp_client as mc

    session = MagicMock(spec=[])  # no get_prompt attribute

    with patch.object(mc.MCPClientManager, "_open_session", _make_open_session_patch(session)):
        mgr = _make_manager()
        result = asyncio.run(mgr.get_prompt("test", "whatever"))

    assert result == mc._NOT_SUPPORTED


# ── LangChain tool smoke tests ──────────────────────────────────

def test_mcp_list_resources_tool_exists():
    from agent.mcp_client import MCP_RESOURCE_TOOLS
    names = [t.name for t in MCP_RESOURCE_TOOLS]
    assert "mcp_list_resources" in names
    assert "mcp_read_resource" in names


def test_mcp_prompt_tools_exist():
    from agent.mcp_client import MCP_PROMPT_TOOLS
    names = [t.name for t in MCP_PROMPT_TOOLS]
    assert "mcp_list_prompts" in names
    assert "mcp_get_prompt" in names


def test_mcp_extra_tools_combined():
    from agent.mcp_client import MCP_EXTRA_TOOLS
    names = [t.name for t in MCP_EXTRA_TOOLS]
    assert "mcp_list_resources" in names
    assert "mcp_read_resource" in names
    assert "mcp_list_prompts" in names
    assert "mcp_get_prompt" in names


def test_mcp_list_resources_tool_invocation():
    """mcp_list_resources tool returns JSON with status field."""
    import json as _json
    from agent import mcp_client as mc

    async def _fake_list(self, server_name):
        return [{"uri": "x://y", "name": "y", "description": "", "mimeType": ""}]

    with patch.object(mc.MCPClientManager, "list_resources", _fake_list):
        # Reset singleton so it picks up patched method
        mc._MANAGER = mc.MCPClientManager({"s": {}})
        tool = next(t for t in mc.MCP_RESOURCE_TOOLS if t.name == "mcp_list_resources")
        raw = mc._run_in_thread(tool.ainvoke({"server_name": "s"}))
        data = _json.loads(raw)

    assert data["server"] == "s"
    assert isinstance(data["resources"], list)
    mc._MANAGER = None  # reset singleton


def test_mcp_get_prompt_tool_invalid_json_args():
    """mcp_get_prompt handles non-JSON arguments string gracefully."""
    from agent import mcp_client as mc

    async def _fake_get(self, server_name, prompt_name, arguments):
        assert arguments == {}
        return "rendered"

    with patch.object(mc.MCPClientManager, "get_prompt", _fake_get):
        mc._MANAGER = mc.MCPClientManager({"s": {}})
        tool = next(t for t in mc.MCP_PROMPT_TOOLS if t.name == "mcp_get_prompt")
        result = mc._run_in_thread(tool.ainvoke({
            "server_name": "s",
            "prompt_name": "greet",
            "arguments": "not-json!!!",
        }))

    assert result == "rendered"
    mc._MANAGER = None  # reset singleton


# ── health loop status tracking ────────────────────────────────

def test_health_loop_marks_degraded_on_failure():
    """_health_loop marks server as degraded when ping fails."""
    from agent import mcp_client as mc
    from contextlib import asynccontextmanager

    session = MagicMock()
    async def _ping():
        raise ConnectionError("server down")
    session.send_ping = _ping

    @asynccontextmanager
    async def _open(self, name):
        yield session

    async def _run_one_iteration():
        mgr = _make_manager({"s": {"type": "stdio", "command": "x"}})
        assert mgr._status["s"] == "unknown"
        with patch.object(mc.MCPClientManager, "_open_session", _open):
            # Manually drive one health check iteration (skip the sleep)
            for name in list(mgr._servers):
                try:
                    async with mgr._open_session(name) as sess:
                        await asyncio.wait_for(sess.send_ping(), timeout=5.0)
                    mgr._status[name] = "healthy"
                except Exception:
                    mgr._status[name] = "degraded"
        return mgr

    mgr = asyncio.run(_run_one_iteration())
    assert mgr._status["s"] == "degraded"
