# Tools Reference

ShadowDev provides 65+ tools organized across the Planner (read-only) and Coder (read-write) agents. The Coder inherits all Planner tools plus write operations.

## Tool Access by Role

- **Planner tools** -- available to both Planner and Coder
- **Coder-only tools** -- only available when the Coder agent is active

---

## File Operations (6 tools)

| Tool | Role | Description |
|------|------|-------------|
| `file_read` | Planner | Read file contents with optional line range |
| `file_list` | Planner | List files in a directory |
| `glob_search` | Planner | Find files matching a glob pattern |
| `file_edit` | Coder | Edit a file using search/replace blocks |
| `file_edit_batch` | Coder | Atomic multi-file edit with rollback on failure |
| `file_write` | Coder | Write or create a file |

### file_edit -- Aider-style search/replace

```
file_edit(
  file_path: str,    # Path to file (relative to workspace)
  old_string: str,   # Text to find (must uniquely match)
  new_string: str     # Replacement text
)
```

### file_edit_batch -- Atomic multi-file edits

```
file_edit_batch(
  edits: list[{file_path, old_string, new_string}]
)
```

All edits are validated in memory first. If any match fails, none are applied.

---

## Code Search (3 tools)

| Tool | Role | Description |
|------|------|-------------|
| `code_search` | Planner | Semantic-aware code search (ripgrep-powered) |
| `grep_search` | Planner | Regex pattern search across files |
| `batch_read` | Planner | Read multiple files in parallel |

### grep_search

```
grep_search(
  pattern: str,           # Regex pattern
  path: str = "",         # Directory to search (default: workspace root)
  include: str = "",      # File glob filter (e.g. "*.py")
  max_results: int = 20   # Max matches to return
)
```

---

## Semantic Search (2 tools)

| Tool | Role | Description |
|------|------|-------------|
| `semantic_search` | Planner | Search codebase by meaning using embeddings |
| `index_codebase` | Planner | Index/re-index codebase for semantic search |

Requires an embedding model (default: `nomic-embed-text` via Ollama). Backend: ChromaDB or Milvus.

---

## LSP -- Language Server Protocol (7 tools)

| Tool | Role | Description |
|------|------|-------------|
| `lsp_definition` | Planner | Go to definition of a symbol |
| `lsp_references` | Planner | Find all references to a symbol |
| `lsp_hover` | Planner | Get type info and docs for a symbol |
| `lsp_symbols` | Planner | List all symbols in a file |
| `lsp_diagnostics` | Planner | Get errors and warnings for a file |
| `lsp_go_to_definition` | Planner | Alternative go-to-definition |
| `lsp_find_references` | Planner | Alternative find-references |

Currently supports Python via pyright. Install: `npm install -g pyright` or `pip install pyright`.

---

## Git (13 tools)

### Read-only (Planner + Coder)

| Tool | Description |
|------|-------------|
| `git_status` | Show working tree status |
| `git_diff` | Show changes (staged/unstaged/between refs) |
| `git_log` | Show commit history |
| `git_show` | Show a specific commit |
| `git_blame` | Show line-by-line authorship |

### Write -- Local (Coder only)

| Tool | Description |
|------|-------------|
| `git_add` | Stage files for commit |
| `git_commit` | Create a commit with message |
| `git_branch` | Create, switch, or delete branches |
| `git_stash` | Stash/pop working changes |

### Write -- Remote (Coder only)

| Tool | Description |
|------|-------------|
| `git_push` | Push commits to remote (safety checks on force-push) |
| `git_pull` | Pull changes from remote |
| `git_fetch` | Fetch remote refs |
| `git_merge` | Merge branches |

---

## Code Analysis (4 tools)

| Tool | Role | Description |
|------|------|-------------|
| `code_quality` | Planner | Cyclomatic complexity, long functions, nesting depth, quality score |
| `dep_graph` | Planner | Python import dependency tree with circular import detection |
| `code_analyze` | Planner | AST-based structural analysis (classes, functions, imports) |
| `context_build` | Planner | Auto-discover relevant files for a task description |

### code_quality

```
code_quality(
  file_path: str,
  include_todos: bool = False   # Include TODO/FIXME locations
)
```

Returns: quality score (0-100), grade (A-F), function complexity, parameter counts, nesting depth.

---

## Testing (1 tool)

| Tool | Role | Description |
|------|------|-------------|
| `run_tests` | Coder | Auto-detect and run test framework |

```
run_tests(
  path: str = "",             # Test file or directory
  framework: str = "auto",    # pytest|jest|vitest|cargo|go|make
  pattern: str = "",          # Test name filter
  timeout: int = 60           # Max seconds
)
```

Supported frameworks: pytest, jest, vitest, cargo test, go test, make test.

---

## Memory (4 tools)

| Tool | Role | Description |
|------|------|-------------|
| `memory_save` | Planner | Save a key-value fact with tags |
| `memory_search` | Planner | Full-text search across saved memories |
| `memory_list` | Planner | List all memory entries |
| `memory_delete` | Planner | Delete a memory entry by key |

Stored in SQLite with FTS5 at `data/memory.db`. Persists across sessions.

```
memory_save(key="auth-pattern", value="Uses JWT with refresh tokens", tags=["auth", "security"])
memory_search(query="authentication")
```

---

## Subagents (4 tools)

| Tool | Role | Description |
|------|------|-------------|
| `task_explore` | Planner | Sequential exploration subtask |
| `task_explore_parallel` | Planner | Run up to 5 explore tasks concurrently |
| `task_general` | Coder | General-purpose subtask (all planner tools) |
| `task_review` | Coder | Code review subtask (diagnostics + tests + quality) |

Subagents use the fast model, max 15 steps, temperature 0.2.

---

## Web (2 tools)

| Tool | Role | Description |
|------|------|-------------|
| `webfetch` | Planner | Fetch and extract content from a URL |
| `web_search` | Planner | Search the web via DuckDuckGo |

---

## Planning & Communication (5 tools)

| Tool | Role | Description |
|------|------|-------------|
| `plan_enter` | Planner | Enter structured planning mode |
| `plan_exit` | Planner | Exit planning mode |
| `todo_read` | Planner | Read the current TODO list |
| `todo_write` | Planner | Update the TODO list |
| `question` | Planner | Ask the user a question (human-in-the-loop via LangGraph interrupt) |

---

## Agent Coordination (2 tools)

| Tool | Role | Description |
|------|------|-------------|
| `handoff_to_coder` | Planner | Hand off execution to the Coder agent with instructions |
| `handoff_to_planner` | Coder | Hand back to the Planner for design guidance |

---

## Skills (6 tools)

| Tool | Role | Description |
|------|------|-------------|
| `skill_invoke` | Planner | Load and execute a markdown workflow skill |
| `skill_list` | Planner | List available skills and installed plugins |
| `hub_search` | Planner | Search the community Skill Hub |
| `skill_create` | Coder | Create a new markdown skill |
| `skill_install` | Coder | Install a skill from the Hub or URL |
| `skill_remove` | Coder | Remove an installed skill |

See [Skills Guide](skills-guide.md).

---

## GitHub (6 tools)

| Tool | Role | Description |
|------|------|-------------|
| `github_list_issues` | Planner | List repository issues |
| `github_list_prs` | Planner | List pull requests |
| `github_get_pr` | Planner | Get PR details, diff, and comments |
| `github_create_issue` | Coder | Create a new issue |
| `github_create_pr` | Coder | Create a pull request |
| `github_comment` | Coder | Comment on an issue or PR |

Requires `GITHUB_TOKEN`. Auto-detects repository from `git remote get-url origin`.

---

## GitLab (6 tools)

| Tool | Role | Description |
|------|------|-------------|
| `gitlab_list_issues` | Planner | List project issues |
| `gitlab_list_mrs` | Planner | List merge requests |
| `gitlab_get_mr` | Planner | Get MR details |
| `gitlab_create_issue` | Coder | Create a new issue |
| `gitlab_create_mr` | Coder | Create a merge request |
| `gitlab_comment` | Coder | Comment on an issue or MR |

Requires `GITLAB_TOKEN`. Supports self-hosted GitLab via `GITLAB_INSTANCE_URL`.

---

## Multimodal Input (2 tools)

| Tool | Role | Description |
|------|------|-------------|
| `voice_input` | Planner | Accept voice/audio input |
| `image_input` | Planner | Accept image input for analysis |

---

## Terminal (1 tool)

| Tool | Role | Description |
|------|------|-------------|
| `terminal_exec` | Coder | Execute shell commands with timeout and output capture |

```
terminal_exec(
  command: str,        # Shell command to execute
  cwd: str = "",       # Working directory (default: workspace root)
  timeout: int = 0     # Max seconds (0 = use TOOL_TIMEOUT)
)
```

Includes a denylist for destructive commands (`rm -rf /`, `dd if=`, fork bombs, etc.). When `SANDBOX_ENABLED=True`, commands run inside a Docker container. See [Security](security.md).

---

## Communication (1 tool)

| Tool | Role | Description |
|------|------|-------------|
| `reply_to_user` | Both | Send a message to the user |

The agent must use this tool for all conversational output. Raw text output is not displayed.
