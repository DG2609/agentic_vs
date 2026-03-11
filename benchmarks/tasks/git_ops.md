---
name: git_ops
expected_tools:
  - git_status
  - git_log
  - git_diff
max_time_s: 45
difficulty: easy
---

# Git Operations Benchmark

Check git status, show recent commits, and show the diff of the latest commit.

## Instructions

1. Run `git status` to see the current state of the working tree.
2. Run `git log` to show the 5 most recent commits with their messages.
3. Run `git diff` to show the diff of the latest commit (HEAD~1..HEAD).

Report a summary of the repository state: which files are modified/untracked, what the recent commits are about, and what changed in the latest commit.

## Acceptance criteria

- The agent invokes `git_status`, `git_log`, and `git_diff`.
- The agent produces a coherent summary of the repository state.
