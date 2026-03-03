---
name: commit
description: Stage changes, write a conventional commit message, and commit
version: "1.0"
---

## Commit Workflow

You are preparing a git commit for the current workspace. Follow these steps precisely.

### Step 1 — Inspect current state

!`git status --short`

!`git diff --stat HEAD`

### Step 2 — Review the staged/unstaged diff

!`git diff HEAD`

### Step 3 — Determine what to stage

Stage all tracked modifications (do NOT stage untracked files unless clearly intentional):

```
git add -u
```

If specific files should be staged selectively, stage them individually.

### Step 4 — Write the commit message

Use **Conventional Commits** format:

```
<type>(<scope>): <short summary>

[optional body — what changed and why, wrapped at 72 chars]

[optional footer — BREAKING CHANGE, issue refs, co-authors]
```

Types: `feat` · `fix` · `refactor` · `test` · `docs` · `chore` · `perf` · `style`

Rules:
- Summary line ≤ 72 characters
- Use imperative mood ("add", not "added")
- Body: explain *why*, not just *what*
- Reference issues with `Closes #N` or `Refs #N` in the footer

### Step 5 — Commit

```
git commit -m "<your message>"
```

### Step 6 — Verify

!`git log --oneline -5`

$ARGUMENTS
