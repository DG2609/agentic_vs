# Configuration

All settings are read from environment variables or a `.env` file in the project root. Configuration is validated at startup using Pydantic `BaseSettings`.

## LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | Backend: `ollama`, `openai`, `anthropic`, `google`, `groq`, or `azure` |

Each provider has a main model and an optional fast model. The fast model is used for subagents and summarization to reduce cost. If the fast model is empty, it falls back to the main model.

## Provider-Specific Settings

### Ollama

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:14b` | Main reasoning model |
| `OLLAMA_FAST_MODEL` | _(same as main)_ | Cheaper model for subagents/summarization |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model for semantic search |

### OpenAI

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | _(empty)_ | Required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o` | Main model |
| `OPENAI_FAST_MODEL` | `gpt-4o-mini` | Subagent/summarization model |

### Anthropic

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(empty)_ | Required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Main model |
| `ANTHROPIC_FAST_MODEL` | _(same as main)_ | Fast model |

### Google Gemini

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_API_KEY` | _(empty)_ | Required when `LLM_PROVIDER=google` |
| `GOOGLE_MODEL` | `gemini-2.0-flash` | Main model |
| `GOOGLE_FAST_MODEL` | _(same as main)_ | Fast model |

### Groq

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | _(empty)_ | Required when `LLM_PROVIDER=groq` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Main model |
| `GROQ_FAST_MODEL` | _(same as main)_ | Fast model |

### Azure OpenAI

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | _(empty)_ | Required when `LLM_PROVIDER=azure` |
| `AZURE_OPENAI_ENDPOINT` | _(empty)_ | Azure endpoint URL |
| `AZURE_OPENAI_MODEL` | `gpt-4o` | Deployment name |
| `AZURE_OPENAI_FAST_MODEL` | _(same as main)_ | Fast deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-10-21` | API version |

## Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port (1-65535) |
| `API_KEY` | _(empty)_ | If set, clients must send this in `x-api-key` header |
| `AGENT_TIMEOUT` | `0` | Max seconds per agent run (0 = unlimited) |

## Workspace

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE_DIR` | `./workspace` | Root directory for all file operations |
| `RULES_FILENAMES` | `AGENTS.md,CLAUDE.md,COPILOT.md,.cursorrules` | Project rules files to inject |

## Tool Tuning

| Variable | Default | Range | Description |
|----------|---------|-------|-------------|
| `TOOL_TIMEOUT` | `30` | 5-300 | Seconds per tool execution |
| `MAX_TERMINAL_OUTPUT` | `10000` | 1000+ | Max chars from terminal output |
| `MAX_OUTPUT_LINES` | `2000` | 100+ | Max lines per tool output |
| `MAX_OUTPUT_BYTES` | `51200` | 1024+ | Max bytes per tool output (50KB) |
| `RIPGREP_PATH` | `rg` | -- | Path to ripgrep binary |

## Context Management

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_MESSAGES_BEFORE_SUMMARY` | `20` | Fallback compaction threshold (message count) |
| `COMPACTION_BUFFER` | `20000` | Trigger compaction when tokens exceed `model_limit - buffer` |
| `PRUNE_MINIMUM` | `20000` | Skip output pruning below this token count |
| `PRUNE_PROTECT` | `40000` | Protect recent messages within this token window |

## Vector DB

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_BACKEND` | `chroma` | `chroma` (no Docker) or `milvus` (Docker required) |

## Container Sandbox

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_ENABLED` | `False` | Run terminal commands inside Docker container |
| `SANDBOX_IMAGE` | `python:3.12-slim` | Docker image for sandbox |
| `SANDBOX_NETWORK` | `none` | Network mode: `none`, `bridge`, or `host` |
| `SANDBOX_MEMORY` | `512m` | Memory limit (Docker format) |
| `SANDBOX_CPUS` | `1.0` | CPU quota |
| `SANDBOX_PIDS_LIMIT` | `100` | Max processes (10-10000) |
| `SANDBOX_READONLY` | `False` | Mount workspace read-only |

## Hooks

| Variable | Default | Description |
|----------|---------|-------------|
| `HOOKS_FILE` | _(empty)_ | Path to hooks config JSON file |

See [Hooks Guide](hooks-guide.md) for the config format.

## GitHub / GitLab

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | _(empty)_ | GitHub personal access token for GitHub tools |
| `GITLAB_TOKEN` | _(empty)_ | GitLab personal access token for GitLab tools |
| `GITLAB_INSTANCE_URL` | `https://gitlab.com` | Self-hosted GitLab URL |

## MCP Servers

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_SERVERS` | `{}` | JSON dict of MCP server configurations |

See [MCP Guide](mcp-guide.md) for the config format.

## Model Context Limits

Known context window sizes are configured in `MODEL_CONTEXT_LIMITS`. If your model is not listed, a warning is emitted at startup and compaction uses a default of 32768 tokens.

```python
MODEL_CONTEXT_LIMITS = {
    "qwen2.5-coder:14b": 32768,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "claude-sonnet-4-20250514": 200000,
    "gemini-2.0-flash": 1048576,
    "llama-3.3-70b-versatile": 131072,
    # ... more models
}
```

## Example `.env` Files

### OpenAI Setup

```ini
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
OPENAI_FAST_MODEL=gpt-4o-mini
WORKSPACE_DIR=/home/user/my-project
API_KEY=my-secret-key
```

### Ollama Setup (Local)

```ini
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:14b
WORKSPACE_DIR=/home/user/my-project
```

### Anthropic + Sandbox

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-20250514
WORKSPACE_DIR=/home/user/my-project
SANDBOX_ENABLED=True
SANDBOX_NETWORK=none
```
