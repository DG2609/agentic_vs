# Getting Started

This guide covers installation, first run, and the three ways to use ShadowDev: interactive CLI, TUI, and headless mode.

## Prerequisites

- Python 3.12+
- One of: Ollama (local), OpenAI API key, Anthropic API key, Google API key, Groq API key, or Azure OpenAI endpoint
- Optional: Docker (for container sandbox and Milvus vector DB)
- Optional: Node.js (for MCP servers, pyright LSP)

## Installation

### Option 1 -- pip (local)

```bash
git clone https://github.com/yourname/shadowdev.git
cd shadowdev

# Install dependencies
pip install -r requirements.txt

# Copy and configure
cp .env.example .env
# Edit .env -- set LLM_PROVIDER, API keys, WORKSPACE_DIR, etc.
```

If using Ollama, start it and pull models:

```bash
ollama serve &
ollama pull qwen2.5-coder:14b      # main model
ollama pull nomic-embed-text        # for semantic search
```

### Option 2 -- Docker

```bash
git clone https://github.com/yourname/shadowdev.git
cd shadowdev

cp .env.example .env
# Edit .env

# Start Ollama + ShadowDev
docker compose up

# Or OpenAI mode (no Ollama container needed)
docker compose up shadowdev
```

Docker Compose profiles:

| Command | What starts |
|---------|-------------|
| `docker compose up` | ShadowDev + Ollama |
| `docker compose up shadowdev` | ShadowDev only (use cloud LLM) |
| `docker compose --profile milvus up` | ShadowDev + Ollama + Milvus vector DB |

### Option 3 -- pip install (as package)

```bash
pip install shadowdev
# or with optional providers:
pip install shadowdev[anthropic,google,groq]
```

## First Run

### Interactive CLI

```bash
python cli.py
```

This starts the Rich-powered interactive CLI with:
- Streaming LLM output rendered as markdown
- Tool execution spinners with elapsed time
- Mode switching (Alt+1 Plan, Alt+2 Code, Alt+3 Doc)
- Status bar showing provider, model, and workspace

CLI commands:
- Type your message and press Enter
- `/plan <message>` -- force Planner mode for this message
- `/code <message>` -- force Coder mode
- `/doc <message>` -- documentation-priority mode
- `/exit` or Ctrl+D -- quit

### TUI Mode

```bash
python cli.py --tui
```

Launches a full-screen terminal UI built with Textual. Requires the `textual` package.

### Web Server

```bash
python server/main.py
```

Starts the Socket.IO + aiohttp server (default: `http://localhost:8000`). Connect with any Socket.IO client or the included web UI.

### Headless / CI Mode

```bash
python cli.py -p "Refactor the utils module to use dataclasses"
```

Runs a single prompt non-interactively and exits. See [CI/Headless Mode](ci-headless.md) for full details.

## CLI Arguments

| Argument | Description |
|----------|-------------|
| `-p`, `--prompt PROMPT` | Run in headless mode with this prompt |
| `--session-id ID` | Session ID for multi-turn persistence |
| `--agent planner\|coder` | Starting agent mode (default: planner) |
| `--output-format text\|json\|stream-json` | Output format for headless mode |
| `--timeout SECONDS` | Max execution time (0 = no limit) |
| `--allowed-tools TOOLS` | Comma-separated tool whitelist |
| `--tui` | Launch full-screen TUI mode |
| `--resume SESSION_ID` | Resume a previous session |

## Verifying the Installation

```bash
# Check all tools load
python -c "
from agent.graph import ALL_TOOLS
print(f'{len(ALL_TOOLS)} tools loaded OK')
"

# Run the test suite
pytest tests/ -v
```

## Semantic Search Setup

Semantic search requires indexing your codebase first:

```bash
# Via the agent
python cli.py -p "Index the codebase for semantic search"

# Or in an interactive session, just ask:
# > index_codebase
```

ChromaDB stores embeddings in `data/chroma_db/`. Only changed files are re-indexed on subsequent runs.

## Setting Up a Workspace

Set `WORKSPACE_DIR` in your `.env` to the root of the project you want to work on:

```ini
WORKSPACE_DIR=/home/user/my-project
```

All file operations are sandboxed to this directory. The agent cannot read or write files outside the workspace boundary.

## Next Steps

- [Configuration](configuration.md) -- all environment variables
- [Tools Reference](tools-reference.md) -- what the agent can do
- [Skills Guide](skills-guide.md) -- extend with custom workflows
