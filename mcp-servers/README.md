# MCP Server Configurations

Pre-built configuration files for popular [Model Context Protocol](https://modelcontextprotocol.io/) servers. Drop any of these into your ShadowDev setup to extend the agent with external tools.

## Quick Start

### Option 1: Environment Variable

Set the `MCP_SERVERS` environment variable to a JSON object. You can paste the contents of any `.json` file from this directory:

```bash
export MCP_SERVERS='{
  "filesystem": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"],
    "access": "both"
  }
}'
```

### Option 2: `.env` File

Add the JSON (on a single line) to your `.env` file:

```
MCP_SERVERS={"filesystem":{"type":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/home/user/projects"],"access":"both"}}
```

### Option 3: Combine Multiple Servers

Use `everything.json` as a starting point, or merge configs manually. The top-level keys are server names and must be unique:

```json
{
  "github": { ... },
  "slack": { ... },
  "postgres": { ... }
}
```

## Configuration Format

Each server entry supports these fields:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Transport: `"stdio"` (spawn a process) or `"sse"` (connect to HTTP SSE endpoint) |
| `command` | stdio only | Command to run (e.g., `"npx"`, `"node"`, `"python"`) |
| `args` | stdio only | List of command-line arguments |
| `url` | sse only | SSE endpoint URL |
| `headers` | sse only | HTTP headers (e.g., `{"Authorization": "Bearer TOKEN"}`) |
| `env` | No | Environment variables passed to the spawned process |
| `access` | No | Tool visibility: `"both"` (default), `"read"` (Planner only), `"write"` (Coder only) |

### Access Levels

- **`"both"`** (default) — Tools are available to both Planner and Coder agents.
- **`"read"`** — Tools are added to the Planner; Coder inherits Planner tools, so they are effectively available to both.
- **`"write"`** — Tools are added to the Coder only. Use this for servers that modify external state (create issues, send messages, write files).

## Available Configs

| File | Server | Description |
|------|--------|-------------|
| `github.json` | GitHub | Issues, PRs, repos, file contents |
| `slack.json` | Slack | Channels, messages, search |
| `postgres.json` | PostgreSQL | Query databases, inspect schemas |
| `filesystem.json` | Filesystem | Read/write files outside workspace |
| `sqlite.json` | SQLite | Query SQLite databases |
| `brave-search.json` | Brave Search | Web and local search |
| `puppeteer.json` | Puppeteer | Browser automation, screenshots |
| `memory.json` | Memory | Persistent knowledge graph |
| `fetch.json` | Fetch | HTTP requests to any URL |
| `everything.json` | Combined | Multiple servers in one config |

## Prerequisites

All stdio-based servers require Node.js 18+ and `npx`. Install them with:

```bash
# macOS / Linux
curl -fsSL https://fnm.vercel.app/install | bash
fnm install 22

# Windows
winget install Schniz.fnm
fnm install 22
```

Some servers need API keys or tokens. See the `env` field in each config file for required variables.

## SSE Transport Example

If you run an MCP server as a standalone HTTP service:

```json
{
  "my-server": {
    "type": "sse",
    "url": "http://localhost:3000/sse",
    "headers": {
      "Authorization": "Bearer YOUR_TOKEN"
    },
    "access": "both"
  }
}
```

## Troubleshooting

- **"mcp package is not installed"** — Run `pip install mcp` or `pip install shadowdev[mcp]`.
- **Server fails to connect** — Check that the command exists (`npx --version`), required env vars are set, and the server package is accessible.
- **Tools not appearing** — Check the agent startup logs for MCP-related warnings. Set `LOGLEVEL=DEBUG` for verbose output.
- **Timeout errors** — MCP tool discovery has a 60-second timeout. Slow servers may need a dedicated SSE endpoint instead of stdio.
