# CI/Headless Mode

ShadowDev can run non-interactively as part of CI/CD pipelines, scripts, and automation workflows. Headless mode executes a single prompt and exits with a meaningful exit code.

## Basic Usage

```bash
python cli.py -p "Refactor the utils module to use dataclasses"
```

This runs the agent on the prompt, prints output, and exits.

## CLI Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `-p`, `--prompt` | The prompt to execute | (required) |
| `--session-id` | Session ID for multi-turn context | new UUID |
| `--agent` | Starting agent: `planner` or `coder` | `planner` |
| `--output-format` | `text`, `json`, or `stream-json` | `text` |
| `--timeout` | Max seconds (0 = no limit) | `0` |
| `--allowed-tools` | Comma-separated tool whitelist | all tools |

## Output Formats

### text (default)

Prints `reply_to_user` messages to stdout and errors to stderr:

```bash
$ python cli.py -p "What files are in the src directory?"
The src directory contains:
- main.py
- utils.py
- config.py
```

### json

Prints a single JSON object after execution completes:

```bash
$ python cli.py -p "List all Python files" --output-format json
```

```json
{
  "success": true,
  "messages": ["The project contains 12 Python files..."],
  "tool_calls": [
    {"tool": "file_list", "args": {"directory": "."}, "run_id": "abc-123"},
    {"tool": "reply_to_user", "args": {"message": "..."}, "run_id": "def-456"}
  ],
  "errors": [],
  "elapsed_seconds": 5.23
}
```

### stream-json

Prints one JSON object per line as events occur:

```bash
$ python cli.py -p "Analyze main.py" --output-format stream-json
{"type": "tool_start", "tool": "file_read", "args": {"file_path": "main.py"}, "run_id": "abc"}
{"type": "tool_end", "tool": "file_read", "run_id": "abc", "output": "import sys..."}
{"type": "message", "content": "The main.py file contains..."}
{"type": "done", "success": true, "exit_code": 0, "elapsed_seconds": 3.14, "error_count": 0}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success -- agent completed without errors |
| `1` | Error -- agent encountered an unrecoverable error |
| `124` | Timeout -- execution exceeded the `--timeout` limit (POSIX convention) |

## Timeouts

```bash
# 5-minute timeout
python cli.py -p "Run the full test suite and fix failures" --timeout 300
```

When the timeout is reached, the agent is cancelled, an error is logged, and exit code 124 is returned.

## Tool Whitelisting

Restrict which tools the agent can use:

```bash
# Only allow read operations
python cli.py -p "Analyze the codebase" \
  --allowed-tools "file_read,file_list,code_search,grep_search,code_analyze"
```

Tools not in the whitelist are silently skipped.

## Multi-Turn Sessions

Use `--session-id` to maintain context across multiple headless runs:

```bash
# First run: analyze
python cli.py -p "Analyze the auth module" --session-id my-task

# Second run: continue with the context from the first run
python cli.py -p "Now refactor it based on your analysis" --session-id my-task --agent coder
```

## GitHub Actions Integration

ShadowDev includes a GitHub Actions workflow (`.github/workflows/shadowdev.yml`) that triggers on:
- `/shadowdev` comment on issues
- `shadowdev` label added to issues
- PR review requested from `shadowdev`

### Workflow Example

```yaml
name: ShadowDev Agent
on:
  issue_comment:
    types: [created]

jobs:
  agent:
    if: contains(github.event.comment.body, '/shadowdev')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run ShadowDev
        env:
          LLM_PROVIDER: openai
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          WORKSPACE_DIR: ${{ github.workspace }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          PROMPT="${{ github.event.comment.body }}"
          PROMPT="${PROMPT#/shadowdev }"
          python cli.py \
            -p "$PROMPT" \
            --agent planner \
            --output-format json \
            --timeout 300

      - name: Post result
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            // Read and post results back as a comment
```

### Exit Code Handling in CI

```yaml
- name: Run agent
  id: agent
  continue-on-error: true
  run: python cli.py -p "$PROMPT" --timeout 300

- name: Handle result
  run: |
    if [ "${{ steps.agent.outcome }}" == "failure" ]; then
      echo "Agent failed or timed out"
      exit 1
    fi
```

## Scripting Examples

### Batch Processing

```bash
#!/bin/bash
# Process multiple files
for file in src/*.py; do
  echo "Analyzing: $file"
  python cli.py -p "Review $file for code quality issues" \
    --output-format json \
    --timeout 60 \
    --agent planner > "reports/$(basename $file).json"
done
```

### Automated Code Review

```bash
# Get changed files from git
CHANGED=$(git diff --name-only HEAD~1)

python cli.py -p "Review these changed files for bugs and quality issues: $CHANGED" \
  --agent planner \
  --output-format json \
  --timeout 120
```

### Pre-Commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit
STAGED=$(git diff --cached --name-only --diff-filter=ACMR)

if [ -n "$STAGED" ]; then
  result=$(python cli.py -p "Check these staged files for obvious bugs: $STAGED" \
    --output-format json \
    --timeout 30 \
    --allowed-tools "file_read,code_quality,lsp_diagnostics" 2>&1)

  if echo "$result" | grep -q '"success": false'; then
    echo "ShadowDev found issues. Review before committing."
    exit 1
  fi
fi
```

## Environment Variables for CI

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export WORKSPACE_DIR=/path/to/repo
export AGENT_TIMEOUT=300           # Safety timeout
export SANDBOX_ENABLED=True        # Isolate terminal commands
export SANDBOX_NETWORK=none        # No network for sandboxed commands
```

## Programmatic Usage (Python)

```python
import asyncio
from agent.headless import run_headless, print_result

result = asyncio.run(run_headless(
    prompt="Analyze the project structure",
    agent="planner",
    output_format="json",
    timeout=120,
))

if result.success:
    for msg in result.messages:
        print(msg)
else:
    for err in result.errors:
        print(f"Error: {err}")

print(f"Elapsed: {result.elapsed:.1f}s")
print(f"Tool calls: {len(result.tool_calls)}")
```
