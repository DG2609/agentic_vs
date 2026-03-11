# Hooks Guide

The hook system lets you intercept tool calls and lifecycle events to customize agent behavior. Hooks can block tools, modify arguments, transform outputs, and react to session events.

## Hook Types

### Tool Hooks

Tool hooks fire before or after a tool executes. They match tool names using fnmatch glob patterns.

| Event | When | Can Do |
|-------|------|--------|
| `pre_tool_use` | Before tool execution | Block execution, modify arguments |
| `post_tool_use` | After tool execution | Modify output, inject messages |

### Lifecycle Hooks

Lifecycle hooks fire on session-level events. They do not match tool names.

| Event | When | Special Behavior |
|-------|------|------------------|
| `session_start` | Start of interactive/headless session | Fire-and-forget |
| `session_end` | Session exits | Fire-and-forget |
| `user_prompt_submit` | Before each user prompt is processed | Can return `{"modified_prompt": "..."}` |
| `stop` | After agent completes a run | Fire-and-forget |
| `subagent_start` | Before a subagent task executes | Fire-and-forget |
| `subagent_stop` | After a subagent task completes | Fire-and-forget |
| `pre_compact` | Before conversation compaction | Fire-and-forget |
| `post_compact` | After conversation compaction | Fire-and-forget |

## Configuration

### JSON Config File

Create a JSON file and set `HOOKS_FILE` in your `.env`:

```ini
HOOKS_FILE=/path/to/hooks.json
```

The file contains a list of hook definitions:

```json
[
  {
    "event": "pre_tool_use",
    "pattern": "terminal_exec",
    "command": "python /path/to/validate_command.py",
    "name": "command-validator"
  },
  {
    "event": "post_tool_use",
    "pattern": "file_write",
    "command": "python /path/to/audit_writes.py",
    "name": "write-auditor"
  },
  {
    "event": "session_start",
    "command": "python /path/to/on_session_start.py",
    "name": "session-logger"
  }
]
```

### Hook Fields

| Field | Required | Description |
|-------|----------|-------------|
| `event` | Yes | `pre_tool_use`, `post_tool_use`, or lifecycle event name |
| `pattern` | No | fnmatch glob for tool names (tool hooks only, default: `*`) |
| `command` | Yes* | Shell command to execute |
| `handler` | Yes* | Python callable (programmatic registration only) |
| `name` | No | Human-readable name for logging |

*One of `command` or `handler` is required.

### Pattern Matching (Tool Hooks)

Patterns use Python's `fnmatch` glob syntax:

| Pattern | Matches |
|---------|---------|
| `terminal_exec` | Only `terminal_exec` |
| `file_*` | `file_read`, `file_write`, `file_edit`, etc. |
| `git_*` | All git tools |
| `*` | Every tool |
| `lsp_*` | All LSP tools |

## Shell Hooks

Shell hooks receive a JSON payload on stdin and can return JSON on stdout.

### Pre-tool-use Payload

```json
{
  "event": "pre_tool_use",
  "tool_name": "terminal_exec",
  "tool_args": {"command": "rm -rf /tmp/test", "cwd": ""}
}
```

### Pre-tool-use Response

```json
{
  "block": true,
  "reason": "Dangerous command detected",
  "modified_args": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `block` | bool | If `true`, tool execution is prevented |
| `reason` | string | Reason for blocking (shown to agent) |
| `modified_args` | dict/null | Override tool arguments |
| `modified_output` | string/null | (post_tool_use only) Override tool output |
| `inject_message` | string/null | Add a system message after the tool call |

### Post-tool-use Payload

```json
{
  "event": "post_tool_use",
  "tool_name": "file_write",
  "tool_args": {"file_path": "src/main.py", "content": "..."},
  "output": "File written successfully (first 5000 chars)"
}

```

### Lifecycle Hook Payload

```json
{
  "event": "session_start",
  "thread_id": "abc-123"
}
```

For `user_prompt_submit`, the hook can return:

```json
{
  "modified_prompt": "Enhanced prompt with additional context"
}
```

## Python Hooks (Programmatic)

Register hooks in Python code using `register_hook()`:

```python
from agent.hooks import register_hook, HookResult

# Block dangerous terminal commands
def validate_terminal(tool_name, tool_args):
    cmd = tool_args.get("command", "")
    if "sudo" in cmd:
        return HookResult(block=True, reason="sudo not allowed")
    return HookResult()

register_hook(
    event="pre_tool_use",
    pattern="terminal_exec",
    handler=validate_terminal,
    name="no-sudo"
)

# Log all file writes
async def audit_writes(tool_name, tool_args, output):
    path = tool_args.get("file_path", "")
    print(f"[audit] File written: {path}")
    return HookResult()

register_hook(
    event="post_tool_use",
    pattern="file_*",
    handler=audit_writes,
    name="write-audit"
)

# Modify user prompts
def enhance_prompt(event, payload):
    prompt = payload.get("prompt", "")
    return {"modified_prompt": f"{prompt}\n\nRemember to follow our coding standards."}

register_hook(
    event="user_prompt_submit",
    handler=enhance_prompt,
    name="prompt-enhancer"
)
```

### Handler Signatures

**Pre-tool-use**: `handler(tool_name: str, tool_args: dict) -> HookResult`
**Post-tool-use**: `handler(tool_name: str, tool_args: dict, output: str) -> HookResult`
**Lifecycle**: `handler(event: str, payload: dict) -> dict | None`

Handlers can be sync or async. Async handlers are awaited automatically.

## Hook Execution

### Merging Multiple Hooks

When multiple hooks match the same tool:
- **Pre-hooks**: if any hook blocks, the tool is blocked. The last non-None `modified_args` wins.
- **Post-hooks**: the last non-None `modified_output` wins.

### Parallel Execution

In the `HookedToolNode`, hooks and tools execute in parallel:

1. All pre-hooks run in parallel via `asyncio.gather`
2. Non-blocked tools execute in parallel via `asyncio.gather`
3. All post-hooks run in parallel via `asyncio.gather`
4. Order is preserved via index slots

A blocked tool returns an error message; other tools in the same batch still execute.

### Timeouts

- Shell hooks have a 10-second timeout
- If a shell hook times out, it is treated as a no-op (no block, no modification)

## Utility Functions

```python
from agent.hooks import (
    register_hook,          # Register a new hook
    clear_hooks,            # Remove all hooks
    load_hooks_from_file,   # Load from JSON file
    load_hooks_from_config, # Load from list of dicts
    run_pre_hooks,          # Execute pre-tool hooks (internal)
    run_post_hooks,         # Execute post-tool hooks (internal)
    run_lifecycle_hook,     # Execute lifecycle hooks (internal)
)
```

## Example: Audit Log

```json
[
  {
    "event": "pre_tool_use",
    "pattern": "*",
    "command": "python audit.py",
    "name": "full-audit"
  },
  {
    "event": "session_start",
    "command": "python log_session.py",
    "name": "session-log"
  }
]
```

```python
# audit.py
import json, sys
data = json.load(sys.stdin)
with open("/var/log/shadowdev-audit.jsonl", "a") as f:
    f.write(json.dumps(data) + "\n")
# Return empty = no block, no modification
print("{}")
```
