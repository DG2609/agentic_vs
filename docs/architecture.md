# Architecture

This document covers the internal design of ShadowDev: the LangGraph StateGraph, Planner/Coder swarm pattern, context management, subagent system, and parallel tool execution.

## StateGraph Flow

ShadowDev uses LangGraph's `StateGraph` to orchestrate the agent loop:

```
__start__
    |
    v
agent_node  -----> tool_node ---+
    |  (tool calls?)            |
    |<--------------------------+
    | (no tool calls)
    v
check_compact
    |
    +---> summarize_node ---> __end__
    +-----------------------> __end__
```

### Nodes

| Node | Purpose |
|------|---------|
| `agent_node` | Invokes the LLM with system prompt + message history. Routes to Planner or Coder based on `active_agent` state. |
| `tool_node` | Executes tool calls from the LLM response. Uses `HookedToolNode` when hooks are registered. |
| `check_compact` | Passthrough node for routing -- checks if compaction is needed. |
| `summarize_node` | Compacts conversation history via LLM summarization. |

### Edges

- `__start__` -> `agent_node` (entry point)
- `agent_node` -> `tool_node` (if LLM response contains tool calls)
- `agent_node` -> `check_compact` (if no tool calls -- agent is done)
- `tool_node` -> `agent_node` (loop back after tool execution)
- `check_compact` -> `summarize_node` (if context overflow detected)
- `check_compact` -> `__end__` (if within token budget)
- `summarize_node` -> `__end__`

## Planner/Coder Swarm

The agent operates in two roles with distinct capabilities and system prompts.

### Planner Agent (Senior Software Architect)

- **Tools**: 45+ read-only tools (search, read, LSP, git status/diff/log, memory, code quality)
- **Purpose**: understand the codebase, analyze, plan
- **Workflow**: reads files, searches patterns, checks quality, forms a plan
- **Exits via**: `handoff_to_coder` with detailed instructions

### Coder Agent (Expert Software Engineer)

- **Tools**: 65+ tools (all Planner tools + write operations)
- **Additional tools**: `file_edit`, `file_write`, `terminal_exec`, `run_tests`, `git_add/commit/push`, GitHub/GitLab write ops
- **Purpose**: execute the plan from Planner
- **Workflow**: makes edits, runs tests, commits changes
- **Can return to Planner**: via `handoff_to_planner` if a design decision is needed

### Handoff Mechanism

When the LLM calls `handoff_to_coder` or `handoff_to_planner`, the `agent_node` updates `active_agent` in state. On the next iteration, the node selects the appropriate system prompt and LLM (with correct tools bound).

```python
# In agent_node:
if tc.get("name") == "handoff_to_coder":
    active_agent = "coder"
elif tc.get("name") == "handoff_to_planner":
    active_agent = "planner"
```

## Context Management

ShadowDev uses multiple strategies to stay within model context limits.

### Token-Based Compaction

The primary compaction trigger is token-based:

```
total_tokens > model_context_limit - COMPACTION_BUFFER
```

Where `COMPACTION_BUFFER` defaults to 20,000 tokens. Model limits are looked up from `MODEL_CONTEXT_LIMITS` (e.g., 128K for GPT-4o, 200K for Claude, 1M for Gemini).

### Output Pruning

Before compaction, `_prune_tool_outputs()` replaces old, large tool outputs (>200 tokens) with 800-character previews. Recent messages within the `PRUNE_PROTECT` window are left intact.

This often recovers enough space to avoid full compaction.

### Structured Summarization

When full compaction is needed, `summarize_node` uses a structured template:

```
## Goal
What the user is trying to accomplish

## Key Instructions
Specific requirements or constraints

## Discoveries
Important facts, code patterns, file locations

## Accomplished
What has been completed (with file paths)

## Active Files
Files read/modified with brief context

## Next Steps
What still needs to be done
```

The fast model (lower cost) generates the summary. The last 6 messages are preserved; older messages are deleted and replaced by the summary.

### Doom Loop Detection

If the agent makes 3 identical consecutive tool calls (same name + same arguments), a doom loop is detected. The system classifies the loop type and provides a targeted recovery hint:

| Loop Type | Detection | Recovery Hint |
|-----------|-----------|---------------|
| `tool_error` | Error/exception/failed in results | "Try a different approach" |
| `missing_file` | "not found" / "does not exist" | "Search more broadly for the correct path" |
| `search_no_result` | "no matches" / "0 results" | "Broaden search terms" |
| `unknown` | None of the above | "Reassess approach" |

## LLM Invocation

### Retry Logic

`_invoke_with_retry()` handles transient failures with exponential backoff:
- Retries on: timeout, connection errors, rate limits (429), server errors (502/503), OOM
- Max 3 attempts with 1s, 2s, 4s delays
- Non-retryable errors fail immediately

### Tool Call Repair

`_repair_tool_calls()` normalizes malformed LLM responses:
- String arguments are parsed as JSON
- Unparseable strings are wrapped as `{"input": "..."}`

### Smart Model Routing

Two LLM instances are created at startup:
- **Main model**: used for `agent_node` (both Planner and Coder)
- **Fast model**: used for subagents and summarization (via `_create_llm(fast=True)`)

## Subagent System

Subagents are mini LLM loops that run as tool calls. They use the fast model with constrained parameters.

| Subagent | Mode | Tools | Max Steps |
|----------|------|-------|-----------|
| `task_explore` | Sequential | search, read, code_analyze | 15 |
| `task_explore_parallel` | Concurrent (up to 5) | same | 15 each |
| `task_general` | Sequential | all planner tools | 15 |
| `task_review` | Sequential | diagnostics, tests, quality | 15 |

Settings: `fast=True` model, temperature 0.2.

`task_explore_parallel` uses `asyncio.gather` to run multiple exploration tasks concurrently, each with its own mini loop.

## HookedToolNode

When hooks are registered (`PRE_TOOL_HOOKS` or `POST_TOOL_HOOKS` are non-empty), `HookedToolNode` replaces the standard `ToolNode`.

### Parallel Execution Pipeline

```
Step 1: asyncio.gather(*pre_hooks)     -- all pre-hooks in parallel
Step 2: Partition blocked vs executable
Step 3: asyncio.gather(*tools)         -- all non-blocked tools in parallel
Step 4: asyncio.gather(*post_hooks)    -- all post-hooks in parallel
Step 5: Build ToolMessage list         -- order preserved via index slots
```

Key properties:
- A blocked tool returns an error message; other tools still execute
- Order of results matches the order of tool calls
- If no hooks are registered, falls through to standard `ToolNode` (zero overhead)

## State Schema

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    summary: str                    # Compaction summary
    workspace: str                  # Current workspace path
    active_agent: str               # "planner" or "coder"
    mode: str                       # UI mode hint
    session_turns: int              # Turn counter
    total_tokens_used: int          # Cumulative token count
    completed_steps: list[str]      # Finished task descriptions
```

## Project Rules Injection

At each `agent_node` invocation, the system checks the workspace for project rules files (`AGENTS.md`, `CLAUDE.md`, `COPILOT.md`, `.cursorrules`). Content from these files (up to 10KB each) is appended to the system prompt.

## Graph Compilation

```python
graph = build_graph(checkpointer=MemorySaver())
```

The `build_graph()` function accepts an optional checkpointer for conversation persistence:
- `MemorySaver()` -- in-memory (CLI sessions)
- `AsyncSqliteSaver` -- persistent across restarts (server mode)

The compiled graph supports `astream_events()` for real-time streaming of LLM output and tool execution events.
