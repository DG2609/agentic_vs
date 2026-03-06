"""
MCP (Model Context Protocol) client integration.

Connects to MCP servers defined in config.MCP_SERVERS and registers
their tools into the agent's tool set.

Config format (MCP_SERVERS dict, set via MCP_SERVERS env var as JSON):
    {
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
            "env": {"KEY": "VAL"},
            "access": "both"
        },
        "my-server": {
            "type": "sse",
            "url": "http://localhost:3000/sse",
            "headers": {"Authorization": "Bearer TOKEN"},
            "access": "write"
        }
    }

access values:
    "both" (default)  — tool available to Planner and Coder
    "read"            — Planner only (Coder inherits Planner tools anyway)
    "write"           — Coder only
"""

import asyncio
import concurrent.futures
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Optional mcp package ─────────────────────────────────────
try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False
    ClientSession = None  # type: ignore
    stdio_client = None  # type: ignore
    StdioServerParameters = None  # type: ignore

try:
    from mcp.client.sse import sse_client
    _HAS_SSE = True
except ImportError:
    _HAS_SSE = False
    sse_client = None  # type: ignore

try:
    from pydantic import create_model, Field as PydanticField
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False


# ── JSON Schema → Python type ────────────────────────────────

def _json_schema_to_python_type(schema: dict) -> type:
    """Convert a JSON Schema type string to a Python type."""
    t = schema.get("type", "string")
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        return list
    if t == "object":
        return dict
    return Any


def _build_args_schema(model_name: str, input_schema: dict | None) -> type | None:
    """Build a dynamic Pydantic model from a JSON Schema dict."""
    if not input_schema or not _HAS_PYDANTIC:
        return None
    properties = input_schema.get("properties", {})
    if not properties:
        return None
    required = set(input_schema.get("required", []))

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        field_type = _json_schema_to_python_type(prop_schema)
        description = prop_schema.get("description", "")
        if prop_name in required:
            fields[prop_name] = (field_type, PydanticField(description=description))
        else:
            fields[prop_name] = (
                Optional[field_type],
                PydanticField(default=None, description=description),
            )

    return create_model(model_name, **fields)


# ── Thread-safe async runner ──────────────────────────────────

def _run_in_thread(coro) -> Any:
    """Run an async coroutine in a dedicated thread (safe from any event loop context)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result(timeout=60)


# ── MCP result → string ───────────────────────────────────────

def _extract_text(result) -> str:
    """Extract text from an MCP CallToolResult."""
    parts = []
    for content in getattr(result, "content", []) or []:
        if hasattr(content, "text"):
            parts.append(content.text)
        else:
            parts.append(str(content))
    if getattr(result, "isError", False):
        parts.insert(0, "[MCP tool returned error]")
    return "\n".join(parts) if parts else "(no output)"


# ── Tool factories ────────────────────────────────────────────

def _make_stdio_tool(server_name: str, cfg: dict, mcp_tool):
    """Create a LangChain StructuredTool that calls an MCP stdio server tool."""
    from langchain_core.tools import StructuredTool

    safe_name = f"mcp_{server_name}_{mcp_tool.name}".replace("-", "_")
    description = mcp_tool.description or f"MCP tool '{mcp_tool.name}' from server '{server_name}'"
    input_schema = getattr(mcp_tool, "inputSchema", None)
    args_schema = _build_args_schema(safe_name + "_Args", input_schema)

    command = cfg.get("command", "")
    cmd_args = cfg.get("args", [])
    env = cfg.get("env") or None

    async def _acall(**kwargs: Any) -> str:
        params = StdioServerParameters(command=command, args=cmd_args, env=env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(mcp_tool.name, kwargs)
                    return _extract_text(result)
        except Exception as exc:
            logger.warning("MCP stdio tool '%s' error: %s", safe_name, exc)
            return f"[MCP error: {exc}]"

    def _sync_call(**kwargs: Any) -> str:
        return _run_in_thread(_acall(**kwargs))

    return StructuredTool.from_function(
        func=_sync_call,
        coroutine=_acall,
        name=safe_name,
        description=description,
        args_schema=args_schema,
    )


def _make_sse_tool(server_name: str, cfg: dict, mcp_tool):
    """Create a LangChain StructuredTool that calls an MCP SSE server tool."""
    from langchain_core.tools import StructuredTool

    safe_name = f"mcp_{server_name}_{mcp_tool.name}".replace("-", "_")
    description = mcp_tool.description or f"MCP tool '{mcp_tool.name}' from server '{server_name}'"
    input_schema = getattr(mcp_tool, "inputSchema", None)
    args_schema = _build_args_schema(safe_name + "_Args", input_schema)

    url = cfg.get("url", "")
    headers = cfg.get("headers") or {}

    async def _acall(**kwargs: Any) -> str:
        try:
            async with sse_client(url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(mcp_tool.name, kwargs)
                    return _extract_text(result)
        except Exception as exc:
            logger.warning("MCP SSE tool '%s' error: %s", safe_name, exc)
            return f"[MCP error: {exc}]"

    def _sync_call(**kwargs: Any) -> str:
        return _run_in_thread(_acall(**kwargs))

    return StructuredTool.from_function(
        func=_sync_call,
        coroutine=_acall,
        name=safe_name,
        description=description,
        args_schema=args_schema,
    )


# ── Server discovery ──────────────────────────────────────────

async def _discover_server_tools(server_name: str, cfg: dict) -> list:
    """Connect to one MCP server and return its tools as LangChain tools."""
    server_type = cfg.get("type", "stdio")
    lc_tools: list = []

    try:
        if server_type == "stdio":
            command = cfg.get("command")
            if not command:
                logger.warning("MCP server '%s': 'command' is required for stdio type", server_name)
                return []
            cmd_args = cfg.get("args", [])
            env = cfg.get("env") or None
            params = StdioServerParameters(command=command, args=cmd_args, env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    for mcp_tool in response.tools:
                        lc_tools.append(_make_stdio_tool(server_name, cfg, mcp_tool))

        elif server_type in ("sse", "http"):
            if not _HAS_SSE:
                logger.warning(
                    "MCP server '%s': SSE transport unavailable. Install: pip install mcp[sse]",
                    server_name,
                )
                return []
            url = cfg.get("url")
            if not url:
                logger.warning("MCP server '%s': 'url' is required for sse/http type", server_name)
                return []
            headers = cfg.get("headers") or {}
            async with sse_client(url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    for mcp_tool in response.tools:
                        lc_tools.append(_make_sse_tool(server_name, cfg, mcp_tool))

        else:
            logger.warning("MCP server '%s': unknown type '%s' (expected stdio, sse, http)", server_name, server_type)

    except Exception as exc:
        logger.warning("MCP server '%s' failed to connect: %s", server_name, exc, exc_info=True)

    logger.info("MCP server '%s': loaded %d tools", server_name, len(lc_tools))
    return lc_tools


async def _load_all_mcp_tools_async(mcp_servers: dict) -> tuple[list, list]:
    """Async: connect to all servers, return (planner_tools, coder_only_tools)."""
    planner_tools: list = []
    coder_tools: list = []

    for server_name, cfg in mcp_servers.items():
        tools = await _discover_server_tools(server_name, cfg)
        access = cfg.get("access", "both")
        for tool in tools:
            if access == "write":
                coder_tools.append(tool)  # coder only
            else:
                # "read" or "both": Planner gets it; Coder inherits Planner tools
                planner_tools.append(tool)

    return planner_tools, coder_tools


# ── Public API ────────────────────────────────────────────────

def load_mcp_tools(mcp_servers: dict) -> tuple[list, list]:
    """
    Connect to all configured MCP servers and return LangChain tool lists.

    Returns:
        (planner_tools, coder_only_tools)
        planner_tools  — available to both Planner and Coder
        coder_only_tools — available to Coder only (access="write" servers)

    Returns ([], []) if mcp package is not installed or all servers fail.
    Spawns a dedicated thread, so safe to call from any context.
    """
    if not mcp_servers:
        return [], []

    if not _HAS_MCP:
        logger.warning(
            "MCP_SERVERS is configured but the 'mcp' package is not installed. "
            "Run: pip install mcp"
        )
        return [], []

    try:
        return _run_in_thread(_load_all_mcp_tools_async(mcp_servers))
    except Exception as exc:
        logger.error("Failed to load MCP tools: %s", exc, exc_info=True)
        return [], []
