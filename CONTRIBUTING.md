# Contributing to ShadowDev

Thank you for your interest in contributing to ShadowDev. This guide covers everything you need to get started.

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+ (for MCP servers and the VS Code extension)
- Git
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for code search tools
- Docker (optional, for container sandbox and Milvus vector backend)

### Fork and Clone

```bash
git clone https://github.com/YOUR_USERNAME/shadowdev.git
cd shadowdev
```

### Install Development Dependencies

```bash
# Create a virtual environment
python -m venv env
source env/bin/activate  # Linux/macOS
env\Scripts\activate     # Windows

# Install the package with all optional deps + dev tools
pip install -e ".[all,dev]"
```

This installs the core package plus Anthropic, Google, Groq, MCP, ChromaDB, server, linting, and testing dependencies.

### Environment Setup

Copy the example environment file and fill in your provider keys:

```bash
cp .env.example .env
```

At minimum, configure one LLM provider:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

See `config.py` for the full list of settings and their defaults.

## Project Structure

```
shadowdev/
├── agent/                  # Core agent logic
│   ├── graph.py            # LangGraph StateGraph (entry point)
│   ├── nodes.py            # Agent node, summarization, compaction
│   ├── subagents.py        # Mini LLM loops (explore, review, general)
│   ├── hooks.py            # Pre/post tool hooks + lifecycle events
│   ├── mcp_client.py       # MCP server integration
│   ├── sandbox.py          # Docker container sandbox
│   ├── skill_engine.py     # Markdown workflow skill parser
│   ├── skill_loader.py     # Python plugin loader (skills/_tools/)
│   ├── skill_hub.py        # Community skill hub (search/install)
│   ├── plugin_registry.py  # pip-installed plugin discovery
│   └── tools/              # All 60+ agent tools
│       ├── file_ops.py     # File read/write/edit/batch/glob
│       ├── terminal.py     # Shell command execution
│       ├── code_search.py  # Grep, ripgrep, batch read
│       ├── git.py          # 13 git operations
│       ├── github.py       # GitHub API (issues, PRs, comments)
│       ├── gitlab.py       # GitLab API (issues, MRs, comments)
│       ├── lsp.py          # Language Server Protocol
│       ├── memory.py       # Persistent SQLite + FTS5 memory
│       ├── test_runner.py  # Multi-framework test runner
│       ├── skills.py       # Skill invoke/list/create/hub tools
│       └── ...             # See agent/tools/ for all tools
├── models/
│   ├── state.py            # AgentState (LangGraph state schema)
│   └── tool_schemas.py     # Pydantic schemas for tool arguments
├── skills/
│   ├── *.md                # Markdown workflow skills
│   ├── agents/*.md         # Agent persona skills
│   └── _tools/*.py         # Python tool plugins
├── mcp-servers/            # MCP server config examples
├── tests/                  # pytest test suite
├── extensions/vscode/      # VS Code extension
├── desktop/                # Electron desktop app
├── config.py               # Pydantic Settings (env-based config)
├── cli.py                  # CLI entry point (interactive + headless)
├── main.py                 # FastAPI server entry point
└── pyproject.toml          # Build config, ruff, mypy
```

## Development Workflow

### Running Tests

```bash
# Run the full test suite
pytest

# Run a specific test file
pytest tests/test_file_ops.py

# Run with verbose output
pytest -v

# Run tests matching a pattern
pytest -k "test_git"
```

Tests are scoped to the `tests/` directory via `pytest.ini`. The test suite currently has 310+ tests.

### Linting and Formatting

We use [Ruff](https://docs.astral.sh/ruff/) for both linting and formatting:

```bash
# Check for lint errors
ruff check .

# Auto-fix lint errors
ruff check --fix .

# Format code
ruff format .

# Type checking (optional, not enforced in CI yet)
mypy agent/ models/
```

Ruff is configured in `pyproject.toml`. Key rules: pycodestyle, pyflakes, isort, pyupgrade, bugbear, comprehensions, simplify.

### Running the Agent

```bash
# Interactive CLI
python cli.py

# Headless mode (for CI/scripts)
python cli.py -p "Explain the architecture of this project"

# With a specific provider
LLM_PROVIDER=anthropic python cli.py

# JSON output
python cli.py -p "List all TODO comments" --output-format json
```

## Adding a New Tool

Tools are the primary way to extend ShadowDev. Each tool is a LangChain `StructuredTool` that the agent can call.

### Step 1: Define the Schema

Add a Pydantic model to `models/tool_schemas.py`:

```python
class MyToolArgs(BaseModel):
    """Arguments for my_tool."""
    file_path: str = Field(description="Path to the target file")
    verbose: bool = Field(default=False, description="Include detailed output")
```

### Step 2: Implement the Tool

Create or edit a file in `agent/tools/`. Use the `@tool` decorator:

```python
# agent/tools/my_tool.py
import logging
from langchain_core.tools import tool
from models.tool_schemas import MyToolArgs
from agent.tools.utils import resolve_tool_path
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)


@tool(args_schema=MyToolArgs)
def my_tool(file_path: str, verbose: bool = False) -> str:
    """One-line description the LLM sees when choosing tools.

    Longer description with usage guidance for the LLM.
    """
    safe_path = resolve_tool_path(file_path)
    # ... implementation ...
    return truncate_output(result)
```

Key conventions:
- Use `resolve_tool_path()` for any file path argument (sandboxes to workspace).
- Wrap output with `truncate_output()` to enforce the 50KB cap.
- Use `logging.getLogger(__name__)` for diagnostics, never `print()`.
- The docstring is what the LLM reads to decide when to use the tool.

### Step 3: Register the Tool

In `agent/graph.py`, import and add your tool:

```python
from agent.tools.my_tool import my_tool

# Add to the appropriate list:
# - PLANNER_TOOLS: read-only tools (search, analyze, inspect)
# - CODER_TOOLS: extends PLANNER_TOOLS with write tools (edit, create, execute)
```

Also add it to `_CORE_TOOLS` so skill deduplication works.

### Step 4: Write Tests

Create `tests/test_my_tool.py`:

```python
import pytest
from agent.tools.my_tool import my_tool


def test_my_tool_basic():
    result = my_tool.invoke({"file_path": "test.py"})
    assert "expected substring" in result


def test_my_tool_handles_missing_file():
    result = my_tool.invoke({"file_path": "nonexistent.py"})
    assert "error" in result.lower() or "not found" in result.lower()
```

Every new tool must have tests. Aim for both success and error paths.

## Creating a Skill

ShadowDev has two skill systems: Markdown workflow skills and Python tool plugins.

### Markdown Workflow Skills

These define multi-step workflows the agent follows. Create a `.md` file in `skills/`:

```markdown
---
name: my-workflow
description: What this workflow does
version: "1.0"
---

## Step 1 - Gather Information

!`git status --short`

Analyze the output above.

## Step 2 - Take Action

Based on the analysis, perform the appropriate action.

$ARGUMENTS
```

Key syntax:
- **YAML frontmatter**: `name` (required), `description`, `model`, `subtask`, `version`.
- **`!`command``**: Shell command whose output is injected into the prompt.
- **`$ARGUMENTS`**: Replaced with user-provided arguments at invocation time.

The agent invokes these via the `skill_invoke` tool.

### Python Tool Plugins

For reusable tools, create a `.py` file in `skills/_tools/`:

```python
# skills/_tools/my_plugin.py

__skill_name__    = "My Plugin"
__skill_version__ = "1.0.0"
__skill_access__  = "read"  # "read" = Planner + Coder, "write" = Coder only

from langchain_core.tools import tool
from agent.tools.truncation import truncate_output


@tool
def my_custom_tool(query: str) -> str:
    """Description the LLM sees."""
    return truncate_output(f"Result for: {query}")


__skill_tools__ = [my_custom_tool]
```

Requirements:
- `__skill_tools__` list is required (must contain objects with `.invoke`).
- `__skill_access__` defaults to `"read"` if omitted.
- Tool names must be unique across all core tools and other plugins.
- The file is auto-discovered on startup; no registration needed.

See `skills/_tools/example_skill.py` for a complete template.

### Distributable Plugins (pip-installable)

For plugins distributed via PyPI:

```toml
# pyproject.toml of your plugin package
[project.entry-points."shadowdev.tools"]
my_plugin = "my_package.tools"
```

The module must export `__skill_tools__` (and optionally `__skill_access__`, `__skill_name__`, etc.). ShadowDev discovers these via `importlib.metadata.entry_points`.

## Code Standards

### Python

- **Python 3.12** minimum. Use modern syntax: `X | Y` unions, `match/case`, f-strings.
- **Type hints** on all function signatures. Use `from __future__ import annotations` if needed.
- **Logging** via `logging.getLogger(__name__)`. Never use `print()` in library code.
- **Pydantic** for all data models and tool argument schemas.
- **Docstrings** on all public functions. Tool docstrings double as LLM-facing documentation.
- **Error handling**: catch specific exceptions, return informative error strings from tools (never raise unhandled exceptions that crash the agent loop).

### Security

- Use `resolve_tool_path()` or `resolve_path_safe()` for file paths (prevents directory traversal).
- Never pass raw user input to `subprocess` without validation.
- Strip sensitive environment variables (`*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSWORD*`) from subprocesses where appropriate.
- The terminal tool has a denylist for dangerous commands.

### Tests

- Every new tool, skill, or feature must include tests.
- Use `pytest` with fixtures from `tests/conftest.py`.
- Mock external dependencies (HTTP APIs, file system, subprocesses).
- Aim for both happy-path and error-path coverage.
- Tests must pass in CI (`pytest` with no arguments).

## Pull Request Guidelines

### Before Submitting

1. Run the full test suite: `pytest`
2. Run the linter: `ruff check .`
3. Format your code: `ruff format .`
4. Verify your changes work end-to-end with the CLI if applicable.

### PR Format

- **Title**: Short, descriptive, imperative mood (e.g., "Add Jira MCP server config", "Fix batch edit rollback on Windows").
- **Description**: Explain what changed and why. Link related issues.
- **Tests**: Describe how you tested the change. Include new test files or cases.
- **Breaking changes**: Call out any breaking changes prominently.

### What We Look For

- Does the code follow the project's conventions (logging, path resolution, truncation)?
- Are there tests for new functionality?
- Is the tool docstring clear enough for an LLM to use correctly?
- Are schemas in `models/tool_schemas.py` for core tools (not for plugins)?
- Does the change handle errors gracefully?

## Architecture Overview

ShadowDev uses a **Planner/Coder swarm pattern** built on LangGraph:

```
User → Planner Agent → (handoff) → Coder Agent → (handoff) → Planner Agent → User
              ↓                          ↓
         Read-only tools            Read + Write tools
         (search, analyze)          (edit, execute, commit)
```

- **Planner**: Analyzes the request, searches code, builds plans. Has access to read-only tools.
- **Coder**: Implements changes, runs tests, commits. Has access to all tools.
- **Compaction**: When context grows too large, `summarize_node` compresses the conversation.
- **Hooks**: Pre/post tool execution hooks can modify arguments, block tools, or transform output.
- **MCP**: External tool servers connected via the Model Context Protocol.

The graph is defined in `agent/graph.py`. For detailed architecture documentation, see the `docs/` directory.

## Getting Help

- Open an [issue](https://github.com/shadowdev/shadowdev/issues) for bugs or feature requests.
- Check existing issues and PRs before creating duplicates.
- For questions about architecture or design decisions, start a discussion in Issues.
