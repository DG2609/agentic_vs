# ShadowDev Benchmarks

Automated benchmarks for measuring ShadowDev agent performance across common coding tasks.

## What the benchmarks measure

Each benchmark task runs the agent in headless mode (no human interaction) and captures:

- **Wall time**: Total elapsed seconds from prompt submission to completion.
- **Token usage**: Approximate token count consumed during the run.
- **Tool calls**: Number and types of tool invocations.
- **Task completion**: Whether expected files were created and expected tools were invoked.
- **Success rate**: Binary pass/fail based on completion criteria.

## Task categories

| Task | Difficulty | Description |
|------|-----------|-------------|
| `file_edit` | easy | Create a file and apply edits (type hints) |
| `search` | easy | Find functions matching a pattern in a codebase file |
| `multi_file` | medium | Create three coordinated files for a CRUD app |
| `test_run` | medium | Write a module, write tests, and execute them |
| `git_ops` | easy | Run git status, log, and diff operations |

Task definitions live in `tasks/*.md` with YAML frontmatter specifying `name`, `expected_tools`, `max_time_s`, and `difficulty`.

## Prerequisites

- Python 3.12+
- A configured LLM provider (set `LLM_PROVIDER` and the matching API key in `.env`)
- Project dependencies installed: `pip install -r requirements.txt`
- `rich` for table output: `pip install rich`

## Running benchmarks

Run all tasks:

```bash
python benchmarks/run_benchmark.py
```

Run specific tasks:

```bash
python benchmarks/run_benchmark.py --tasks file_edit,search
```

Override the model:

```bash
python benchmarks/run_benchmark.py --model gpt-4o
```

Save results to JSON:

```bash
python benchmarks/run_benchmark.py --output results.json
```

Set a per-task timeout (seconds):

```bash
python benchmarks/run_benchmark.py --timeout 120
```

## Interpreting results

### Summary table

After all tasks complete, a table is printed showing each task's status, elapsed time, tool call count, and whether completion checks passed. Example:

```
┏━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Task       ┃ Status    ┃ Time (s) ┃ Tool Calls ┃ Complete ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━┩
│ file_edit  │ PASS      │    12.3  │         6  │ Yes      │
│ search     │ PASS      │     8.1  │         3  │ Yes      │
│ multi_file │ PASS      │    22.7  │        11  │ Yes      │
│ test_run   │ FAIL      │    45.0  │        15  │ No       │
│ git_ops    │ PASS      │     5.4  │         4  │ Yes      │
└────────────┴───────────┴──────────┴────────────┴──────────┘
```

### JSON output

When `--output` is used, results are written as a JSON file with this structure:

```json
{
  "run_id": "uuid",
  "timestamp": "ISO-8601",
  "model": "gpt-4o",
  "provider": "openai",
  "results": [
    {
      "task": "file_edit",
      "status": "pass",
      "elapsed_s": 12.3,
      "tool_calls": 6,
      "tools_used": ["file_write", "file_edit", "reply_to_user"],
      "expected_tools_hit": true,
      "expected_files_found": true,
      "errors": []
    }
  ],
  "summary": {
    "total": 5,
    "passed": 4,
    "failed": 1,
    "avg_time_s": 18.7,
    "total_tool_calls": 39
  }
}
```

### What constitutes a pass

A task **passes** when:

1. The agent completes without error or timeout.
2. All tools listed in `expected_tools` were invoked at least once.
3. All files listed in `expected_files` exist after execution (if specified).
4. Execution completed within `max_time_s`.

### Comparing across models / providers

Run the same benchmark suite against different models and compare the JSON output files. Key metrics to compare:

- **Avg time**: Faster models complete tasks quicker.
- **Tool efficiency**: Fewer tool calls for the same result indicates better planning.
- **Pass rate**: Higher is better; indicates the model can follow instructions reliably.
