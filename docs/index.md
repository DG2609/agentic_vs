# ShadowDev Documentation

ShadowDev is a production-grade, self-hosted AI coding assistant built on LangGraph. It uses a **Planner/Coder swarm** architecture with 65+ specialized tools, persistent cross-session memory, LSP integration, multi-framework test running, and human-in-the-loop support.

## Key Features

- **Planner/Coder Swarm** -- two-agent architecture with automatic handoff between planning and execution phases
- **65+ Tools** -- file operations, code search, git, LSP, testing, memory, GitHub/GitLab, MCP, and more
- **6 LLM Providers** -- OpenAI, Ollama, Anthropic, Google Gemini, Groq, Azure OpenAI
- **Persistent Memory** -- SQLite FTS5 knowledge base that persists across sessions
- **Skills System** -- markdown workflow skills, Python tool plugins, pip-installable plugin registry, community Skill Hub
- **MCP Integration** -- connect to any Model Context Protocol server (stdio + SSE transports)
- **Hook System** -- pre/post tool-use hooks and lifecycle event hooks for customization
- **Container Sandbox** -- Docker-based isolation for terminal commands
- **Headless/CI Mode** -- run from scripts, pipelines, and GitHub Actions
- **Multiple Interfaces** -- Web UI (Socket.IO), CLI, TUI, VS Code extension, Desktop app

## Quick Links

| Topic | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Installation, first run, CLI usage |
| [Configuration](configuration.md) | All environment variables and provider setup |
| [Tools Reference](tools-reference.md) | Complete reference for all 65+ tools |
| [Skills Guide](skills-guide.md) | Workflow skills, plugins, and the Skill Hub |
| [MCP Guide](mcp-guide.md) | Model Context Protocol server integration |
| [Hooks Guide](hooks-guide.md) | Pre/post tool hooks and lifecycle events |
| [API Reference](api-reference.md) | HTTP endpoints and Socket.IO events |
| [Architecture](architecture.md) | StateGraph flow, context management, internals |
| [CI/Headless Mode](ci-headless.md) | Running ShadowDev in CI/CD pipelines |
| [Security](security.md) | Sandboxing, denylist, env scrubbing, isolation |

## Architecture at a Glance

```
User (WebSocket / CLI / TUI)
        |
        v
  server/main.py          <-- Socket.IO + aiohttp
        |
        v
  LangGraph StateGraph
  +-----------------------------------+
  |  __start__                        |
  |      |                            |
  |      v                            |
  |  agent_node --> tool_node ---+    |
  |      |    (tool calls?)     |    |
  |      |<---------------------+    |
  |      | (no tool calls)           |
  |      v                            |
  |  check_compact                    |
  |      |                            |
  |      +---> summarize --> __end__  |
  |      +-----------------> __end__  |
  +-----------------------------------+
        |
  Planner Agent (read, analyze, plan)
        |  handoff_to_coder
        v
  Coder Agent (read, write, git, test)
        |  handoff_to_planner (if needed)
        v
  Subagents (parallel explore / review)
```

## Supported LLM Providers

| Provider | Main Model Default | Fast Model Default | Package |
|----------|-------------------|-------------------|---------|
| Ollama | `qwen2.5-coder:14b` | same | `langchain-ollama` |
| OpenAI | `gpt-4o` | `gpt-4o-mini` | `langchain-openai` |
| Anthropic | `claude-sonnet-4-20250514` | same | `langchain-anthropic` |
| Google | `gemini-2.0-flash` | same | `langchain-google-genai` |
| Groq | `llama-3.3-70b-versatile` | same | `langchain-groq` |
| Azure OpenAI | `gpt-4o` | same | `langchain-openai` |

## Project Rules Injection

Place any of these files in your workspace root to automatically inject project context into every agent run:

- `AGENTS.md` -- agent-specific instructions
- `CLAUDE.md` -- Claude-style instructions
- `COPILOT.md` -- Copilot-style instructions
- `.cursorrules` -- Cursor-style rules

## License

MIT. See [LICENSE](../LICENSE) for details.
