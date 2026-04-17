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
import json
import logging
from contextlib import asynccontextmanager
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


# ── MCP Client Manager ────────────────────────────────────────

_NOT_SUPPORTED = "Not supported by this MCP server version"

_HEALTH_LOOP_TASK: Optional[asyncio.Task] = None


class MCPClientManager:
    """Manages persistent MCP client connections with Resources, Prompts, and health-check support.

    Maintains a registry of server configs and connection state, exposing
    async methods for list_resources / read_resource / list_prompts / get_prompt.
    A background health-check loop pings every server every 60 s.
    """

    def __init__(self, mcp_servers: dict):
        self._servers: dict[str, dict] = mcp_servers or {}
        # server_name → "healthy" | "degraded" | "unknown"
        self._status: dict[str, str] = {name: "unknown" for name in self._servers}

    # ── Internal: open a short-lived session ─────────────────

    @asynccontextmanager
    async def _open_session(self, server_name: str):
        """Async context manager that opens a ClientSession for the named server."""
        if not _HAS_MCP:
            raise RuntimeError("mcp package not installed")
        cfg = self._servers.get(server_name)
        if cfg is None:
            raise KeyError(f"Unknown MCP server: {server_name!r}")
        server_type = cfg.get("type", "stdio")

        if server_type == "stdio":
            command = cfg.get("command")
            if not command:
                raise ValueError(f"MCP server {server_name!r}: 'command' is required")
            params = StdioServerParameters(
                command=command,
                args=cfg.get("args", []),
                env=cfg.get("env") or None,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        elif server_type in ("sse", "http"):
            if not _HAS_SSE:
                raise RuntimeError("SSE transport unavailable. Install: pip install mcp[sse]")
            url = cfg.get("url")
            if not url:
                raise ValueError(f"MCP server {server_name!r}: 'url' is required")
            headers = cfg.get("headers") or {}
            async with sse_client(url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            raise ValueError(f"MCP server {server_name!r}: unknown type {server_type!r}")

    # ── Session wrapper ───────────────────────────────────────

    async def _with_session(
        self,
        server_name: str,
        op_name: str,
        body,
        *,
        on_unsupported,
        on_error,
        **log_ctx,
    ):
        """Run ``body(session)`` inside a session, normalising error handling.

        - ``AttributeError`` → session lacks the method: return ``on_unsupported``
          (don't mark the server as degraded — the server is healthy, it just
          doesn't support this capability).
        - Any other exception → warn with op_name + log_ctx, mark degraded,
          and return ``on_error(exc)``.
        - Success → mark healthy and return body's return value.
        """
        try:
            async with self._open_session(server_name) as session:
                result = await body(session)
                self._status[server_name] = "healthy"
                return result
        except AttributeError:
            return on_unsupported
        except Exception as exc:
            suffix = " " + " ".join(f"{k}={v!r}" for k, v in log_ctx.items()) if log_ctx else ""
            logger.warning("[mcp] %s '%s' failed:%s %s", op_name, server_name, suffix, exc)
            self._status[server_name] = "degraded"
            return on_error(exc)

    # ── Resources ─────────────────────────────────────────────

    async def list_resources(self, server_name: str) -> list[dict]:
        """List available resources from an MCP server.

        Returns a list of dicts with keys: uri, name, description, mimeType.
        Returns a sentinel list if the server doesn't support resources.
        """
        async def _body(session):
            response = await session.list_resources()
            return [
                {
                    "uri": str(getattr(r, "uri", "")),
                    "name": str(getattr(r, "name", "")),
                    "description": str(getattr(r, "description", "") or ""),
                    "mimeType": str(getattr(r, "mimeType", "") or ""),
                }
                for r in getattr(response, "resources", [])
            ]

        return await self._with_session(
            server_name, "list_resources", _body,
            on_unsupported=[{"error": _NOT_SUPPORTED}],
            on_error=lambda exc: [{"error": str(exc)}],
        )

    async def read_resource(self, server_name: str, uri: str) -> str:
        """Read a resource by URI. Returns text content (or base64 blob fallback)."""
        async def _body(session):
            response = await session.read_resource(uri)
            parts = []
            for content in getattr(response, "contents", []):
                if hasattr(content, "text") and content.text is not None:
                    parts.append(content.text)
                elif hasattr(content, "blob") and content.blob is not None:
                    parts.append(f"[blob:{getattr(content, 'mimeType', 'application/octet-stream')}]")
                else:
                    parts.append(str(content))
            return "\n".join(parts) if parts else "(no content)"

        return await self._with_session(
            server_name, "read_resource", _body,
            on_unsupported=_NOT_SUPPORTED,
            on_error=lambda exc: f"[MCP error: {exc}]",
            uri=uri,
        )

    # ── Prompts ───────────────────────────────────────────────

    async def list_prompts(self, server_name: str) -> list[dict]:
        """List available prompt templates from an MCP server.

        Returns a list of dicts with keys: name, description, arguments.
        """
        async def _body(session):
            response = await session.list_prompts()
            result = []
            for p in getattr(response, "prompts", []):
                args = [
                    {
                        "name": str(getattr(a, "name", "")),
                        "description": str(getattr(a, "description", "") or ""),
                        "required": bool(getattr(a, "required", False)),
                    }
                    for a in (getattr(p, "arguments", []) or [])
                ]
                result.append({
                    "name": str(getattr(p, "name", "")),
                    "description": str(getattr(p, "description", "") or ""),
                    "arguments": args,
                })
            return result

        return await self._with_session(
            server_name, "list_prompts", _body,
            on_unsupported=[{"error": _NOT_SUPPORTED}],
            on_error=lambda exc: [{"error": str(exc)}],
        )

    async def get_prompt(self, server_name: str, prompt_name: str, arguments: dict | None = None) -> str:
        """Get a rendered prompt template. Returns the messages as plain text."""
        if arguments is None:
            arguments = {}

        async def _body(session):
            response = await session.get_prompt(prompt_name, arguments)
            parts = []
            for msg in getattr(response, "messages", []):
                role = getattr(msg, "role", "")
                content_obj = getattr(msg, "content", None)
                if content_obj is None:
                    continue
                text = content_obj.text if hasattr(content_obj, "text") else str(content_obj)
                parts.append(f"[{role}] {text}")
            return "\n".join(parts) if parts else "(empty prompt)"

        return await self._with_session(
            server_name, "get_prompt", _body,
            on_unsupported=_NOT_SUPPORTED,
            on_error=lambda exc: f"[MCP error: {exc}]",
            prompt=prompt_name,
        )

    # ── Health check loop ─────────────────────────────────────

    async def _health_loop(self):
        """Background loop: ping every configured server every 60 s."""
        while True:
            await asyncio.sleep(60)
            for name in list(self._servers):
                try:
                    async with self._open_session(name) as session:
                        await asyncio.wait_for(session.send_ping(), timeout=5.0)
                    self._status[name] = "healthy"
                except Exception as exc:
                    logger.warning("[mcp] server %s health check failed: %s", name, exc)
                    self._status[name] = "degraded"

    def start_health_loop(self):
        """Schedule the health loop onto the running event loop (non-blocking)."""
        global _HEALTH_LOOP_TASK
        try:
            loop = asyncio.get_running_loop()
            _HEALTH_LOOP_TASK = loop.create_task(self._health_loop())
        except RuntimeError:
            # No running event loop — will be started lazily
            pass


# ── Singleton manager (populated lazily) ──────────────────────

_MANAGER: Optional["MCPClientManager"] = None


def get_manager() -> "MCPClientManager":
    """Return the global MCPClientManager, initialised from config.MCP_SERVERS."""
    global _MANAGER
    if _MANAGER is None:
        import config as _cfg
        _MANAGER = MCPClientManager(_cfg.MCP_SERVERS)
    return _MANAGER


# ── LangChain tools: Resources ────────────────────────────────

def _make_mcp_resource_tools() -> list:
    """Build the mcp_list_resources and mcp_read_resource LangChain tools."""
    from langchain_core.tools import tool as lc_tool
    from agent.tools.truncation import truncate_output

    @lc_tool
    def mcp_list_resources(server_name: str) -> str:
        """List available MCP resources from a named server.

        Returns a JSON object containing:
        - 'server': the server name
        - 'status': current health status of the server
        - 'resources': list of resource descriptors (uri, name, description, mimeType)
        """
        mgr = get_manager()
        resources = _run_in_thread(mgr.list_resources(server_name))
        output = {
            "server": server_name,
            "status": mgr._status.get(server_name, "unknown"),
            "resources": resources,
        }
        return json.dumps(output, indent=2)

    @lc_tool
    def mcp_read_resource(server_name: str, uri: str) -> str:
        """Read a resource by URI from a named MCP server. Returns text content (truncated to 50 KB)."""
        mgr = get_manager()
        content = _run_in_thread(mgr.read_resource(server_name, uri))
        return truncate_output(content)

    return [mcp_list_resources, mcp_read_resource]


# ── LangChain tools: Prompts ──────────────────────────────────

def _make_mcp_prompt_tools() -> list:
    """Build the mcp_list_prompts and mcp_get_prompt LangChain tools."""
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def mcp_list_prompts(server_name: str) -> str:
        """List available MCP prompt templates from a named server. Returns JSON."""
        mgr = get_manager()
        prompts = _run_in_thread(mgr.list_prompts(server_name))
        return json.dumps({"server": server_name, "prompts": prompts}, indent=2)

    @lc_tool
    def mcp_get_prompt(server_name: str, prompt_name: str, arguments: str = "{}") -> str:
        """Render an MCP prompt template by name.

        Args:
            server_name: Name of the MCP server as configured in MCP_SERVERS.
            prompt_name: Name of the prompt template to render.
            arguments: JSON string of key/value arguments for the template (default "{}").

        Returns the rendered messages as plain text, one per line prefixed with the role.
        """
        try:
            args_dict = json.loads(arguments)
            if not isinstance(args_dict, dict):
                args_dict = {}
        except (json.JSONDecodeError, ValueError):
            args_dict = {}
        mgr = get_manager()
        return _run_in_thread(mgr.get_prompt(server_name, prompt_name, args_dict))

    return [mcp_list_prompts, mcp_get_prompt]


# ── Exported tool lists ───────────────────────────────────────

MCP_RESOURCE_TOOLS: list = _make_mcp_resource_tools()
MCP_PROMPT_TOOLS: list = _make_mcp_prompt_tools()
# Combined convenience export
MCP_EXTRA_TOOLS: list = MCP_RESOURCE_TOOLS + MCP_PROMPT_TOOLS
