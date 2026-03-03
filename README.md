# ShadowDev Agentic IDE

A production-grade, self-hosted AI coding assistant powered by LangGraph. Features a
**Planner/Coder swarm** architecture with 55 specialized tools, persistent cross-session
memory, LSP integration, multi-framework test running, and human-in-the-loop support.

## Architecture

```
User (WebSocket / CLI)
        │
        ▼
  server/main.py          ← Socket.IO + aiohttp
        │
        ▼
  LangGraph StateGraph
  ┌─────────────────────────────────┐
  │  __start__                      │
  │      │                          │
  │      ▼                          │
  │  agent_node ──► tool_node ──┐   │
  │      │    (tool calls?)     │   │
  │      │◄────────────────────┘   │
  │      │ (no tool calls)         │
  │      ▼                          │
  │  check_compact                  │
  │      │                          │
  │      ├──► summarize ──► __end__ │
  │      └──────────────► __end__   │
  └─────────────────────────────────┘
        │
  Planner Agent (read, analyze, plan)
        │  handoff_to_coder
        ▼
  Coder Agent (read, write, git, test)
        │  handoff_to_planner (if needed)
        ▼
  Subagents (parallel explore / review)
```

## Features

| Category | Tools | Highlights |
|----------|-------|-----------|
| **File ops** | file_read/write/edit/edit_batch/list/glob | Atomic multi-file edits with rollback |
| **Code search** | code_search, grep_search, batch_read | Ripgrep-powered, parallel reads |
| **Semantic search** | semantic_search, index_codebase | ChromaDB (default) or Milvus |
| **LSP** | definition, references, hover, symbols, diagnostics | Auto-detect pylsp / pyright |
| **Git** | 13 tools (status/diff/log/show/blame/add/commit/branch/stash/push/pull/fetch/merge) | Safety checks on force-push |
| **Code analysis** | code_quality, dep_graph, code_analyze, context_build | Python AST + regex for JS/TS/Go/Rust/Java/C |
| **Testing** | run_tests | Auto-detect pytest / jest / vitest / cargo / go |
| **Memory** | memory_save/search/list/delete | SQLite FTS5, cross-session |
| **Subagents** | task_explore, task_explore_parallel, task_general, task_review | asyncio.gather parallelism |
| **Web** | webfetch, web_search | DuckDuckGo integration |
| **Human-in-loop** | question | LangGraph interrupt() — pauses until user answers |
| **Planning** | plan_enter/exit, todo_read/write | Structured planning mode |

**Total: 55 tools** across Planner (39) and Coder (55) roles.

## Quick Start

### Option 1 — Docker (recommended)

```bash
# Clone
git clone https://github.com/yourname/shadowdev.git
cd shadowdev

# Copy and edit config
cp .env.example .env
# Edit .env: set LLM_PROVIDER, OPENAI_API_KEY or OLLAMA_MODEL, etc.

# Start (Ollama + ShadowDev)
docker compose up

# Open in browser
open http://localhost:8000
```

### Option 2 — Local Python

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env

# 3. Start Ollama (if using local LLM)
ollama serve &
ollama pull qwen2.5-coder:14b    # main model
ollama pull nomic-embed-text     # for semantic search

# 4. Run server
python server/main.py

# OR run CLI
python cli.py
```

## Configuration

All settings are read from environment variables or `.env` file.

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | `ollama` or `openai` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:14b` | Main reasoning model |
| `OLLAMA_FAST_MODEL` | _(same)_ | Cheaper model for subagents/summarization |
| `OPENAI_API_KEY` | _(empty)_ | Required if `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o` | Main model |
| `OPENAI_FAST_MODEL` | `gpt-4o-mini` | Subagent/summarization model |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model for semantic search |

### Server settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `API_KEY` | _(empty)_ | Optional: clients must send this key to connect |
| `AGENT_TIMEOUT` | `0` | Max seconds per agent run (0 = unlimited) |
| `WORKSPACE_DIR` | `./workspace` | Default workspace directory |

### Vector DB settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_BACKEND` | `chroma` | `chroma` (no Docker) or `milvus` (Docker) |

### Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_TIMEOUT` | `30` | Seconds per tool call |
| `MAX_OUTPUT_LINES` | `2000` | Max lines per tool output |
| `MAX_OUTPUT_BYTES` | `51200` | Max bytes per tool output (50 KB) |
| `MAX_MESSAGES_BEFORE_SUMMARY` | `20` | Fallback compaction threshold |
| `COMPACTION_BUFFER` | `20000` | Token headroom before compaction |

### Example `.env`

```ini
# OpenAI mode
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
OPENAI_FAST_MODEL=gpt-4o-mini

# Workspace
WORKSPACE_DIR=/path/to/your/project

# Security (optional)
API_KEY=my-secret-key
AGENT_TIMEOUT=300
```

## Project Rules Injection

Place any of these files in your workspace root to automatically inject project context
into every agent run:

- `AGENTS.md` — agent-specific instructions
- `CLAUDE.md` — Claude-style instructions
- `COPILOT.md` — Copilot-style instructions
- `.cursorrules` — Cursor-style rules

## Semantic Search Setup

Semantic search requires an indexed codebase:

```
# Via CLI:
> index_codebase

# Via agent message:
"Index the codebase for semantic search"
```

ChromaDB stores embeddings in `data/chroma_db/`. Only changed files are re-indexed
on subsequent runs.

## Socket.IO Events

### Client → Server

| Event | Payload | Description |
|-------|---------|-------------|
| `chat:message` | `{message, thread_id, mode, api_key?}` | Send a message |
| `chat:stop` | `{thread_id}` | Cancel running generation |
| `agent:resume` | `{thread_id, answer}` | Resume after human-in-the-loop |

### Server → Client

| Event | Payload | Description |
|-------|---------|-------------|
| `connected` | `{sid}` | Connection established |
| `text` | `{content}` | Streamed LLM text chunk |
| `tool:start` | `{tool_name, tool_id, arguments}` | Tool execution started |
| `tool:end` | `{tool_name, tool_id, status, result}` | Tool execution completed |
| `file:diff` | `{path, original, modified}` | File was edited (show diff) |
| `agent:question` | `{thread_id, text, options, multiple}` | Agent is asking a question |
| `agent:interrupt` | `{thread_id, text, options}` | Execution paused for answer |
| `index:progress` | `{indexed, skipped, errors, total, active}` | Indexing progress |
| `title` | `{content}` | Suggested conversation title |
| `done` | `{stopped, interrupted}` | Generation finished |
| `error` | `{content}` | Error message |

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Server health + config |
| `GET` | `/api/files?path=` | List workspace files |
| `GET` | `/api/file?path=` | Read file content |
| `POST` | `/api/file` | Write file `{path, content}` |
| `POST` | `/api/file/revert` | Revert AI edit `{path, content}` |
| `GET` | `/api/workspace` | Current workspace path |
| `POST` | `/api/workspace` | Set workspace `{workspace}` |
| `GET` | `/api/model` | Current LLM info |
| `GET` | `/api/search?q=` | Search files in workspace |
| `GET` | `/api/git/status` | Git status |

## CLI Usage

```bash
python cli.py [--workspace /path/to/project] [--thread-id my-thread]
```

Commands in CLI:
- Type your message and press Enter
- `/clear` — clear conversation
- `/mode planner` / `/mode coder` — switch agent role
- Ctrl+C — stop generation
- Ctrl+D / `exit` — quit

## Docker Compose Profiles

```bash
# Default: ShadowDev + Ollama
docker compose up

# With Milvus vector DB (requires extra containers)
docker compose --profile milvus up

# Scale: run without Ollama (OpenAI mode)
docker compose up shadowdev
```

## Development

```bash
# Run tests
pytest tests/ -v

# Check code quality
python -c "
from agent.tools.code_quality import code_quality
print(code_quality.invoke({'file_path': 'agent/nodes.py'}))
"

# Verify all tools load
python -c "
from agent.graph import ALL_TOOLS
print(f'{len(ALL_TOOLS)} tools loaded OK')
"
```

## Architecture Details

### Planner/Coder Swarm

The agent starts as **Planner** (read-only):
1. Reads relevant files, understands codebase
2. Searches for patterns, checks code quality
3. Forms a complete plan
4. Calls `handoff_to_coder` with detailed instructions

The **Coder** then executes:
1. Makes surgical edits (`file_edit`) or batch edits (`file_edit_batch`)
2. Runs tests (`run_tests`) to verify
3. Commits changes (`git_add` + `git_commit`)
4. Saves learnings to memory (`memory_save`)
5. Calls `handoff_to_planner` if it hits a design problem

### Context Management

- **Token-based compaction**: triggers when `total_tokens > model_limit - 20000`
- **Output pruning**: old tool outputs (>200 tokens) replaced with preview before LLM call
- **Structured summarization**: `Goal / Key Instructions / Discoveries / Accomplished / Files / Next Steps`
- **Doom loop detection**: 3 identical consecutive tool calls → classified recovery hint

### Subagents

| Agent | Runs | Tools |
|-------|------|-------|
| `task_explore` | Sequential | search, read, code_analyze |
| `task_explore_parallel` | Concurrent (up to 5) | same as above |
| `task_general` | Sequential | all planner tools |
| `task_review` | Sequential | lsp_diagnostics, run_tests, code_quality |

Each subagent is a mini LLM loop (`fast=True` model, max 15 steps, temperature 0.2).

## License

MIT
