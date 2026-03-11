# MCP Integration Guide

ShadowDev integrates with the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) to connect to external tool servers. MCP servers expose tools that are automatically discovered and registered into the agent's tool set at startup.

## Overview

- **Transports**: stdio (subprocess) and SSE (HTTP server-sent events)
- **Dynamic schemas**: tool parameters are converted from JSON Schema to Pydantic models at discovery time
- **Access control**: each server can be configured as read, write, or both
- **Graceful fallback**: if the `mcp` package is not installed, MCP is silently skipped

## Installation

The `mcp` package is optional:

```bash
pip install mcp              # stdio transport
pip install mcp[sse]         # stdio + SSE transport
```

## Configuration

MCP servers are configured via the `MCP_SERVERS` environment variable (JSON) or in your `.env` file:

```ini
MCP_SERVERS='{"filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"], "access": "both"}}'
```

### Config Format

```json
{
  "server-name": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
    "env": {"KEY": "VALUE"},
    "access": "both"
  },
  "remote-server": {
    "type": "sse",
    "url": "http://localhost:3000/sse",
    "headers": {"Authorization": "Bearer TOKEN"},
    "access": "write"
  }
}
```

### Server Fields

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | `"stdio"` or `"sse"` (also accepts `"http"`) |
| `command` | stdio only | Command to spawn the server process |
| `args` | No | Command arguments (list) |
| `env` | No | Environment variables for the subprocess |
| `url` | sse only | HTTP endpoint for the SSE server |
| `headers` | No | HTTP headers (e.g., auth tokens) |
| `access` | No | `"both"` (default), `"read"`, or `"write"` |

### Access Control

| Value | Planner | Coder |
|-------|---------|-------|
| `"both"` (default) | Yes | Yes |
| `"read"` | Yes | Yes (inherits from Planner) |
| `"write"` | No | Yes |

## How It Works

1. At startup, `load_mcp_tools()` is called in `agent/graph.py`
2. For each configured server, ShadowDev connects and calls `list_tools()`
3. Each MCP tool is wrapped as a LangChain `StructuredTool`
4. Tool names are prefixed: `mcp_{server_name}_{tool_name}` (hyphens replaced with underscores)
5. JSON Schema input parameters are converted to dynamic Pydantic models
6. Tools are added to `PLANNER_TOOLS` or `CODER_TOOLS` based on access level

## Example Configs

### Filesystem Server

```json
{
  "filesystem": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/project"],
    "access": "both"
  }
}
```

### GitHub Server

```json
{
  "github": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {"GITHUB_TOKEN": "ghp_..."},
    "access": "write"
  }
}
```

### Brave Search Server

```json
{
  "brave-search": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
    "env": {"BRAVE_API_KEY": "..."},
    "access": "read"
  }
}
```

### Custom SSE Server

```json
{
  "my-api": {
    "type": "sse",
    "url": "http://localhost:8080/sse",
    "headers": {"Authorization": "Bearer my-token"},
    "access": "both"
  }
}
```

### Multiple Servers

```json
{
  "filesystem": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
    "access": "read"
  },
  "github": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {"GITHUB_TOKEN": "ghp_..."},
    "access": "write"
  },
  "custom-api": {
    "type": "sse",
    "url": "http://internal:3000/sse",
    "access": "both"
  }
}
```

## Dynamic Schema Generation

When ShadowDev discovers an MCP tool, it reads the tool's `inputSchema` (JSON Schema) and dynamically creates a Pydantic model. This ensures the LLM sees proper parameter names, types, and descriptions.

JSON Schema type mapping:

| JSON Schema | Python Type |
|-------------|-------------|
| `string` | `str` |
| `integer` | `int` |
| `number` | `float` |
| `boolean` | `bool` |
| `array` | `list` |
| `object` | `dict` |

Required fields become mandatory parameters; optional fields get `None` defaults.

## Error Handling

- If `mcp` is not installed, a warning is logged and no MCP tools are loaded
- If a server fails to connect, its tools are skipped (other servers still load)
- If SSE transport is needed but `mcp[sse]` is not installed, a warning is logged
- Tool invocation errors return `[MCP error: ...]` rather than crashing
- Server connections have a 60-second timeout

## Troubleshooting

**No MCP tools loading:**
- Verify the `mcp` package is installed: `pip install mcp`
- Check `MCP_SERVERS` is valid JSON in your `.env`
- Look for warnings in the log output at startup

**SSE server not connecting:**
- Install SSE support: `pip install mcp[sse]`
- Verify the URL is reachable from the host
- Check that auth headers are correct

**stdio server failing:**
- Ensure the command (e.g., `npx`) is on your PATH
- Check that required env vars are set in the `env` field
- Try running the command manually to verify it starts
