# Security Model

ShadowDev implements defense-in-depth security across workspace sandboxing, terminal command filtering, environment scrubbing, container isolation, and hook-based access control.

## Workspace Sandboxing

All file operations are sandboxed to the configured `WORKSPACE_DIR`. The agent cannot read or write files outside this boundary.

### Path Resolution

Three levels of path resolution exist in `agent/tools/utils.py`:

| Function | Behavior | Used By |
|----------|----------|---------|
| `resolve_path(p)` | Strict -- raises `ValueError` if path escapes workspace | `file_read`, `file_write`, `file_edit` |
| `resolve_path_safe(p)` | Strict -- raises `ValueError` if path escapes workspace | `terminal_exec` (cwd sandboxing) |
| `resolve_tool_path(p)` | Lenient -- clamps to workspace root with warning if path escapes | LSP, code_analyzer, file_ops (glob/list) |

### How It Works

1. Relative paths are joined with `WORKSPACE_DIR`
2. Absolute paths are checked to ensure they fall within `WORKSPACE_DIR`
3. Symlinks are resolved via `os.path.realpath()` to prevent symlink escapes
4. Paths outside the workspace either raise an error (strict) or clamp to workspace root (lenient)

```python
# Example: attempt to escape workspace
resolve_path("../../etc/passwd")
# ValueError: Path '/etc/passwd' is outside workspace '/home/user/project'
```

## Terminal Command Denylist

The `terminal_exec` tool blocks catastrophically destructive commands using regex patterns with whitespace normalization:

| Pattern | Blocks |
|---------|--------|
| `rm -rf /` | Filesystem nuke (and variants like `rm -fr /`) |
| `rm -rf ~` | Home directory nuke |
| `dd if=` | Disk wipe |
| `mkfs.*` | Disk formatting |
| `> /dev/sd*` | Direct disk write |
| `:(){ :\|:& }` | Fork bomb |
| `chmod -R 000 /` | Permission nuke |

Whitespace is normalized before matching, so `rm  -rf  /` cannot bypass `rm -rf /`.

```python
# Blocked with descriptive error:
terminal_exec("rm -rf /")
# "Command blocked: 'rm -rf /' matches a dangerous pattern ('rm -rf /')."
```

## LSP Environment Scrubbing

When spawning LSP subprocesses (pyright), ShadowDev strips sensitive environment variables:

```python
# Variables containing these substrings are removed:
KEY, SECRET, TOKEN, PASSWORD, CREDENTIAL
```

This prevents LSP processes from accessing API keys or other secrets that may be in the agent's environment.

## Container Sandbox

When `SANDBOX_ENABLED=True`, terminal commands run inside an isolated Docker container.

### Isolation Features

| Feature | Config | Default |
|---------|--------|---------|
| Auto-removal | `--rm` | Always |
| Network isolation | `SANDBOX_NETWORK` | `none` (no network access) |
| Memory limit | `SANDBOX_MEMORY` | `512m` |
| CPU limit | `SANDBOX_CPUS` | `1.0` |
| PID limit | `SANDBOX_PIDS_LIMIT` | `100` |
| No privilege escalation | `--security-opt no-new-privileges` | Always |
| Read-only workspace | `SANDBOX_READONLY` | `False` |
| Named containers | For reliable cleanup | Always |

### How It Works

1. At import time, `_check_docker()` verifies Docker is available
2. If Docker is unavailable, falls back to direct execution with a warning
3. Commands run in `SANDBOX_IMAGE` (default: `python:3.12-slim`)
4. The workspace is mounted at `/workspace` inside the container
5. On timeout, the named container is killed by name for reliable cleanup

### Configuration

```ini
SANDBOX_ENABLED=True
SANDBOX_IMAGE=python:3.12-slim
SANDBOX_NETWORK=none           # Full network isolation
SANDBOX_MEMORY=512m
SANDBOX_CPUS=1.0
SANDBOX_PIDS_LIMIT=100
SANDBOX_READONLY=False          # Set True for analysis-only tasks
```

### Platform Notes

- Linux: works natively with Docker
- macOS: requires Docker Desktop
- Windows: requires Docker Desktop with WSL2

## Hook-Based Access Control

The hook system can block tool execution based on custom logic. See [Hooks Guide](hooks-guide.md) for full details.

### Example: Block All File Writes

```json
[
  {
    "event": "pre_tool_use",
    "pattern": "file_write",
    "command": "python block_writes.py",
    "name": "block-writes"
  }
]
```

```python
# block_writes.py
import json, sys
data = json.load(sys.stdin)
print(json.dumps({"block": True, "reason": "File writes are disabled"}))
```

### Example: Restrict Terminal Commands

```python
from agent.hooks import register_hook, HookResult

def restrict_terminal(tool_name, tool_args):
    cmd = tool_args.get("command", "")
    forbidden = ["sudo", "apt", "pip install", "npm install"]
    for word in forbidden:
        if word in cmd:
            return HookResult(block=True, reason=f"'{word}' is not allowed")
    return HookResult()

register_hook("pre_tool_use", pattern="terminal_exec", handler=restrict_terminal)
```

## Memory Sensitive Keyword Warnings

The `memory_save` tool warns when attempting to save data containing sensitive keywords:

```
memory_save(key="db-creds", value="password=secret123")
# Warning: Memory entry may contain sensitive data (keyword: 'password')
```

Keywords checked: `password`, `secret`, `token`, `api_key`, `credential`, `private_key`.

## Dependency Graph Boundary Checking

The `dep_graph` tool checks workspace boundaries when resolving relative imports. A `..` import that would ascend above the workspace root is blocked:

```python
# If workspace is /home/user/project and a file imports from ../../outside,
# the resolver stops at the workspace boundary.
```

## API Authentication

When `API_KEY` is set, the server requires authentication:
- HTTP requests must include `x-api-key` header
- Socket.IO connections must include `api_key` in the connection payload

Without the correct key, requests are rejected.

## Output Truncation

All tool outputs pass through `truncate_output()` which enforces:
- `MAX_OUTPUT_LINES` (default: 2000 lines)
- `MAX_OUTPUT_BYTES` (default: 50KB)
- Large outputs are saved to disk with `chmod 0o600` (Unix only)

This prevents memory exhaustion from runaway tool output and protects output files from other users.

## Security Best Practices

1. **Set `WORKSPACE_DIR`** to the specific project directory, not `/` or `~`
2. **Enable `SANDBOX_ENABLED`** in production to isolate terminal commands
3. **Set `API_KEY`** to prevent unauthorized access to the server
4. **Use `SANDBOX_NETWORK=none`** to prevent sandboxed commands from making network calls
5. **Set `AGENT_TIMEOUT`** to prevent runaway agent loops
6. **Use hooks** to enforce organization-specific security policies
7. **Set `SANDBOX_READONLY=True`** for analysis-only tasks where the agent should not modify files
8. **Review `HOOKS_FILE`** for pre-tool-use hooks that validate commands before execution
9. **Keep `GITHUB_TOKEN` / `GITLAB_TOKEN` scoped** to only the repositories the agent needs
10. **Use `--allowed-tools`** in headless mode to restrict tool access per task

## Threat Model Summary

| Threat | Mitigation |
|--------|-----------|
| LLM tries to read files outside workspace | Path sandboxing with symlink resolution |
| LLM runs destructive shell commands | Denylist + container sandbox |
| LLM exfiltrates secrets via LSP | Environment variable scrubbing |
| LLM spins up resource-consuming processes | PID/memory/CPU limits in sandbox |
| LLM escapes workspace via relative imports | Boundary checking in dep_graph |
| Unauthorized API access | API key authentication |
| Tool output causes memory exhaustion | 50KB output truncation |
| Malicious hooks or plugins | Hooks have 10s timeout; plugins validated at load |
