---
name: search
expected_tools:
  - grep_search
max_time_s: 45
difficulty: easy
---

# Search Benchmark

Find all functions in `agent/nodes.py` that start with an underscore.

## Instructions

1. Search the file `agent/nodes.py` for all function definitions whose names begin with `_` (private/internal functions).
2. Report the list of matching function names.

## Acceptance criteria

- The agent uses a search tool (grep_search, code_search, or file_read) to inspect `agent/nodes.py`.
- The agent produces a list of underscore-prefixed function names found in the file.
