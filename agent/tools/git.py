"""
Tools: Git operations — status, diff, log, commit, add, branch, stash, show, blame.

All operations are scoped to the workspace git repository.

Tool split by role:
  PLANNER (read-only): git_status, git_diff, git_log, git_show, git_blame
  CODER   (write):     git_add, git_commit, git_branch, git_stash,
                       git_push, git_pull, git_fetch, git_merge

Design decisions:
- All commands run in the git repo root (not just workspace dir) for complete picture.
- Paths passed by the agent are validated to stay within workspace via resolve_tool_path.
- No --no-verify, no --force-push, no --allow-unrelated-histories without explicit warning.
- Large outputs go through truncate_output to protect context window.
"""
import os
import subprocess
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path
from models.tool_schemas import (
    GitDiffArgs, GitLogArgs, GitAddArgs, GitCommitArgs,
    GitBranchArgs, GitStashArgs, GitShowArgs, GitBlameArgs,
    GitPushArgs, GitPullArgs, GitFetchArgs, GitMergeArgs,
)


# ─────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────

def _git(args: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[str, str, int]:
    """Run a git command. Returns (stdout, stderr, returncode).

    Uses the workspace dir as cwd by default. Never raises — all errors
    are returned as (empty_stdout, error_message, non-zero_code).
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or config.WORKSPACE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.stdout, result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "git command timed out after 30s", 1
    except FileNotFoundError:
        return "", "git executable not found — is git installed and on PATH?", 1
    except Exception as e:
        return "", f"Unexpected error running git: {e}", 1


def _git_root() -> str | None:
    """Return the absolute path of the git repo root, or None if not in a repo."""
    out, _, code = _git(["rev-parse", "--show-toplevel"])
    if code == 0 and out.strip():
        return out.strip()
    return None


def _repo_cwd() -> str:
    """Return the git repo root for commands that need the full repo context.
    Falls back to workspace if not in a git repo.
    """
    return _git_root() or config.WORKSPACE_DIR


def _ensure_repo() -> str | None:
    """Return an error string if not in a git repo, else None."""
    if _git_root() is None:
        return (
            "Error: not a git repository. "
            "Run 'git init' in your workspace or open a project that is a git repo."
        )
    return None


def _current_branch() -> str:
    """Return current branch name, or '(detached HEAD)' if in detached state."""
    out, _, code = _git(["branch", "--show-current"])
    if code == 0 and out.strip():
        return out.strip()
    # Detached HEAD: show the commit hash instead
    hash_out, _, _ = _git(["rev-parse", "--short", "HEAD"])
    return f"(detached HEAD at {hash_out.strip()})" if hash_out.strip() else "(detached HEAD)"


def _tracking_info(branch: str) -> str:
    """Return upstream tracking info string, e.g. '→ origin/main (↑2 ↓1)'."""
    upstream_out, _, code = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if code != 0:
        return "(no upstream)"

    upstream = upstream_out.strip()
    ab_out, _, ab_code = _git(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if ab_code == 0:
        try:
            ahead, behind = ab_out.strip().split()
            return f"→ {upstream} (↑{ahead} ↓{behind})"
        except ValueError:
            pass
    return f"→ {upstream}"


# ─────────────────────────────────────────────────────────────
# READ-ONLY TOOLS (PLANNER)
# ─────────────────────────────────────────────────────────────

@tool
def git_status() -> str:
    """Show the working tree status — staged, unstaged, and untracked files.

    Displays current branch, upstream tracking info (ahead/behind), and
    a categorized list of all modified files.

    Returns:
        Formatted status with staged, unstaged, and untracked file lists.
    """
    err = _ensure_repo()
    if err:
        return err

    branch = _current_branch()
    tracking = _tracking_info(branch)

    out, stderr, code = _git(["status", "--porcelain=v1"])
    if code != 0:
        return f"Error running git status: {stderr}"

    # Porcelain v1 format:  XY filename
    # X = staged status, Y = unstaged status
    # Special: '??' = untracked, 'R  old -> new' = renamed
    XY_LABELS = {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "U": "unmerged",
    }

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []

    for line in out.splitlines():
        if not line:
            continue
        x = line[0]
        y = line[1]
        path = line[3:]

        if x == "?" and y == "?":
            untracked.append(f"  {path}")
        else:
            if x not in (" ", "?"):
                label = XY_LABELS.get(x, x)
                staged.append(f"  {label:<10s} {path}")
            if y not in (" ", "?"):
                label = XY_LABELS.get(y, y)
                unstaged.append(f"  {label:<10s} {path}")

    result = [f"Branch: {branch}  {tracking}", ""]

    if staged:
        result.append(f"Staged ({len(staged)} files):")
        result.extend(staged)
        result.append("")

    if unstaged:
        result.append(f"Not staged ({len(unstaged)} files):")
        result.extend(unstaged)
        result.append("")

    if untracked:
        result.append(f"Untracked ({len(untracked)} files):")
        # Limit to 20 to avoid flooding context
        result.extend(untracked[:20])
        if len(untracked) > 20:
            result.append(f"  ... and {len(untracked) - 20} more untracked files")
        result.append("")

    if not staged and not unstaged and not untracked:
        result.append("Nothing to commit, working tree clean.")
    else:
        result.append(
            f"Summary: {len(staged)} staged | "
            f"{len(unstaged)} unstaged | "
            f"{len(untracked)} untracked"
        )

    return "\n".join(result)


@tool(args_schema=GitDiffArgs)
def git_diff(file_path: str = "", staged: bool = False, base: str = "") -> str:
    """Show differences in the working tree or between commits.

    Args:
        file_path: Specific file to diff. Empty = diff all changed files.
        staged: Show staged changes (what would be committed). False = unstaged changes.
        base: Compare HEAD against this ref (e.g. 'main', 'HEAD~1', 'abc1234').

    Returns:
        Unified diff with added/removed line counts.
    """
    err = _ensure_repo()
    if err:
        return err

    args = ["diff"]

    if staged:
        args.append("--staged")

    if base:
        # Diff between base and current HEAD (or working tree if no staged flag)
        args.append(base)

    if file_path:
        # Resolve to validate path, but pass the original to git
        resolve_tool_path(file_path)  # just for validation
        args.extend(["--", file_path])

    out, stderr, code = _git(args, cwd=_repo_cwd())
    if code != 0:
        return f"Error running git diff: {stderr}"

    if not out.strip():
        ctx = "staged " if staged else ""
        target = f" in {file_path}" if file_path else ""
        base_ctx = f" vs {base}" if base else ""
        return f"No {ctx}changes{target}{base_ctx}."

    # Count changed lines for a quick summary
    lines = out.splitlines()
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    files_changed = sum(1 for l in lines if l.startswith("diff --git"))

    header = (
        f"Diff ({files_changed} file(s) changed, "
        f"+{added} added, -{removed} removed):\n"
    )
    return truncate_output(header + out)


@tool(args_schema=GitLogArgs)
def git_log(n: int = 15, file_path: str = "", branch: str = "", graph: bool = False) -> str:
    """Show commit history.

    Args:
        n: Number of commits to show (1-100). Default 15.
        file_path: Limit to commits affecting this file.
        branch: Show log for this branch/ref. Empty = current branch.
        graph: Show ASCII branch graph.

    Returns:
        Formatted commit log with hash, author, relative time, and subject.
    """
    err = _ensure_repo()
    if err:
        return err

    if graph:
        args = [
            "log", f"-{n}",
            "--oneline", "--graph", "--decorate",
        ]
    else:
        # Custom format: short_hash | author | relative_time | subject
        args = [
            "log", f"-{n}",
            "--pretty=format:%h|%an|%ar|%s",
        ]

    if branch:
        args.append(branch)

    if file_path:
        resolve_tool_path(file_path)  # validate path
        args.extend(["--", file_path])

    out, stderr, code = _git(args, cwd=_repo_cwd())
    if code != 0:
        return f"Error running git log: {stderr}"

    if not out.strip():
        return "No commits found."

    if graph:
        return truncate_output(f"Git log ({n} commits, graph):\n\n{out}")

    # Format the structured output
    lines = ["Git log (most recent first):\n"]
    for entry in out.strip().splitlines():
        if not entry:
            continue
        parts = entry.split("|", 3)
        if len(parts) == 4:
            short_hash, author, rel_time, subject = parts
            lines.append(f"● {short_hash}  {subject}")
            lines.append(f"  {author}  ·  {rel_time}")
            lines.append("")
        else:
            lines.append(entry)

    return truncate_output("\n".join(lines))


@tool(args_schema=GitShowArgs)
def git_show(ref: str = "HEAD", file_path: str = "") -> str:
    """Show the details of a specific commit — message, author, diff.

    Args:
        ref: Commit hash, branch, tag, or relative ref like 'HEAD~2'. Default: HEAD.
        file_path: Limit diff output to this specific file.

    Returns:
        Commit metadata and diff.
    """
    err = _ensure_repo()
    if err:
        return err

    # First get commit metadata cleanly
    meta_out, _, meta_code = _git([
        "show", ref,
        "--pretty=format:Commit: %H%nAuthor: %an <%ae>%nDate:   %ad%nSubject: %s%n%n%b",
        "--date=format:%Y-%m-%d %H:%M",
        "--stat",
        "--no-patch",
    ])

    if meta_code != 0:
        return f"Error: ref '{ref}' not found or git error."

    # Then get the diff
    diff_args = ["show", ref, "--patch", "--no-stat"]
    if file_path:
        resolve_tool_path(file_path)  # validate
        diff_args.extend(["--", file_path])

    diff_out, stderr, diff_code = _git(diff_args, cwd=_repo_cwd())
    if diff_code != 0:
        return f"Error getting diff for {ref}: {stderr}"

    combined = meta_out.strip() + "\n\n" + diff_out.strip()
    return truncate_output(combined)


@tool(args_schema=GitBlameArgs)
def git_blame(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Show who last modified each line of a file (git blame).

    Useful for understanding the history of a specific section of code
    before modifying it, or to find who introduced a bug.

    Args:
        file_path: File to annotate.
        start_line: First line to show (1-indexed). 0 = from beginning.
        end_line: Last line to show (1-indexed, inclusive). 0 = to end.

    Returns:
        Line-by-line annotation with commit hash, author, date, and content.
    """
    err = _ensure_repo()
    if err:
        return err

    resolved = resolve_tool_path(file_path)
    if not os.path.isfile(resolved):
        return f"Error: file '{file_path}' not found."

    args = ["blame", "--date=short", "-w"]  # -w ignores whitespace changes

    if start_line and end_line:
        args.append(f"-L{start_line},{end_line}")
    elif start_line:
        # Show from start_line to end of file (or +100 lines max without end_line)
        args.append(f"-L{start_line},+100")

    args.append(resolved)

    out, stderr, code = _git(args, cwd=_repo_cwd())
    if code != 0:
        return f"Error running git blame: {stderr}"

    if not out.strip():
        return f"No blame data for '{file_path}'."

    # git blame --date=short output:
    # ^abc1234 (Author Name        2024-01-15  1) line content
    # Parse and reformat to compact table
    lines = []
    for raw in out.splitlines():
        # Find the opening parenthesis
        paren = raw.find("(")
        if paren < 0:
            lines.append(raw)
            continue

        commit_part = raw[:paren].strip().lstrip("^")[:7]  # short hash
        meta_end = raw.find(")")
        if meta_end < 0:
            lines.append(raw)
            continue

        meta = raw[paren + 1:meta_end]
        content = raw[meta_end + 1:]

        # meta = "Author Name        2024-01-15  linenum"
        # Split from right: linenum, date, then rest = author
        meta_parts = meta.rsplit(None, 2)  # split last 2 whitespace tokens
        if len(meta_parts) == 3:
            author_raw, date, linenum = meta_parts
            author = author_raw.strip()[:18]
        else:
            author = meta.strip()[:18]
            date = ""
            linenum = "?"

        lines.append(f"{linenum:>5} | {commit_part:<7} | {date:<10} | {author:<18} |{content}")

    header = (
        f"Blame: {file_path}"
        + (f" (lines {start_line}–{end_line})" if start_line else "")
        + "\n"
        + f"{'Line':>5} | {'Hash':<7} | {'Date':<10} | {'Author':<18} | Content\n"
        + "─" * 80
        + "\n"
    )
    return truncate_output(header + "\n".join(lines))


# ─────────────────────────────────────────────────────────────
# WRITE TOOLS (CODER)
# ─────────────────────────────────────────────────────────────

@tool(args_schema=GitAddArgs)
def git_add(paths: list[str]) -> str:
    """Stage files for the next commit.

    Args:
        paths: Files or patterns to stage. Use ['.'] to stage everything.
               e.g. ['src/main.py', 'tests/'] or ['.']

    Returns:
        What was staged, shown as a diff stat.
    """
    err = _ensure_repo()
    if err:
        return err

    # Validate paths that look like files (not '.', not glob patterns starting with *)
    for p in paths:
        if p == "." or p.startswith("*") or p.startswith(":"):
            continue  # allow "." and globs
        resolved = resolve_tool_path(p)
        ws = os.path.realpath(config.WORKSPACE_DIR)
        if not resolved.startswith(ws):
            return (
                f"Error: path '{p}' resolves outside workspace. "
                f"Only files within '{config.WORKSPACE_DIR}' can be staged."
            )

    _, stderr, code = _git(["add"] + paths, cwd=_repo_cwd())
    if code != 0:
        return f"Error staging files: {stderr}"

    # Show what is now staged
    stat_out, _, _ = _git(["diff", "--staged", "--stat"], cwd=_repo_cwd())
    if stat_out.strip():
        return f"✅ Staged successfully.\n\nStaged changes:\n{stat_out.strip()}"
    return "✅ Files added to staging area (no diff — files may already be up to date)."


@tool(args_schema=GitCommitArgs)
def git_commit(message: str) -> str:
    """Create a git commit with the currently staged changes.

    Only commits what has been staged with git_add. Does NOT stage
    automatically — call git_add first.

    Does NOT skip hooks (--no-verify). If pre-commit hooks fail, fix
    the underlying issue rather than bypassing them.

    Args:
        message: Descriptive commit message explaining what and why.

    Returns:
        New commit hash and summary of what was committed.
    """
    if config.UNDERCOVER_MODE:
        try:
            from agent.undercover import sanitize_message
            message = sanitize_message(message)
        except Exception:
            pass

    err = _ensure_repo()
    if err:
        return err

    # Check there is something staged
    staged_out, _, staged_code = _git(["diff", "--staged", "--stat"], cwd=_repo_cwd())
    if staged_code == 0 and not staged_out.strip():
        return (
            "Nothing staged to commit. "
            "Use git_add to stage files first, then call git_commit."
        )

    out, stderr, code = _git(["commit", "-m", message], cwd=_repo_cwd())
    if code != 0:
        # Common failure: pre-commit hook
        if "hook" in stderr.lower() or "hook" in out.lower():
            return (
                f"❌ Commit blocked by pre-commit hook.\n\n"
                f"{stderr or out}\n\n"
                f"Fix the issues above and try again. "
                f"Do NOT bypass hooks with --no-verify."
            )
        return f"❌ Commit failed: {stderr or out}"

    # Get the short hash of the new commit
    hash_out, _, _ = _git(["rev-parse", "--short", "HEAD"], cwd=_repo_cwd())
    short_hash = hash_out.strip()

    return f"✅ Committed {short_hash}\n\n{out.strip()}"


@tool(args_schema=GitBranchArgs)
def git_branch(action: str, name: str = "", from_ref: str = "") -> str:
    """Manage git branches — list, create, switch, or delete.

    Args:
        action: 'list' | 'create' | 'switch' | 'delete'
        name: Branch name. Required for create / switch / delete.
        from_ref: Base ref for new branch (default: current HEAD).

    Returns:
        Result of the branch operation.
    """
    err = _ensure_repo()
    if err:
        return err

    if action == "list":
        out, stderr, code = _git(["branch", "-a", "-vv"], cwd=_repo_cwd())
        if code != 0:
            return f"Error listing branches: {stderr}"
        return f"Branches:\n{out.strip()}"

    if not name:
        return f"Error: 'name' is required for action '{action}'."

    if action == "create":
        args = ["checkout", "-b", name]
        if from_ref:
            args.append(from_ref)
        out, stderr, code = _git(args, cwd=_repo_cwd())

    elif action == "switch":
        out, stderr, code = _git(["checkout", name], cwd=_repo_cwd())

    elif action == "delete":
        # Use safe delete (-d), not force delete (-D)
        # -d refuses to delete unmerged branches (protects work)
        out, stderr, code = _git(["branch", "-d", name], cwd=_repo_cwd())
        if code != 0 and "not fully merged" in stderr:
            return (
                f"❌ Cannot delete '{name}' — it has unmerged commits.\n\n"
                f"{stderr}\n\n"
                f"Merge or rebase first. If you really want to force-delete, "
                f"run: terminal_exec('git branch -D {name}')"
            )
    else:
        # Should not reach here due to schema validator, but be safe
        return f"Error: unknown action '{action}'."

    if code != 0:
        return f"❌ Error: {stderr or out}"

    verb = {"create": "Created", "switch": "Switched to", "delete": "Deleted"}[action]
    detail = out.strip()
    return f"✅ {verb} branch '{name}'." + (f"\n{detail}" if detail else "")


@tool(args_schema=GitStashArgs)
def git_stash(action: str, message: str = "", index: int = 0) -> str:
    """Manage the git stash — save, pop, list, or drop stash entries.

    Use 'save' to temporarily set aside uncommitted changes.
    Use 'pop' to restore the most recently stashed changes.

    Args:
        action: 'save' | 'pop' | 'list' | 'drop'
        message: Description for the stash entry (used with 'save').
        index: Which stash to pop/drop (0 = most recent).

    Returns:
        Result of the stash operation.
    """
    err = _ensure_repo()
    if err:
        return err

    if action == "list":
        out, _, _ = _git(["stash", "list"], cwd=_repo_cwd())
        return out.strip() or "No stashes saved."

    if action == "save":
        args = ["stash", "push"]
        if message:
            args.extend(["-m", message])
        out, stderr, code = _git(args, cwd=_repo_cwd())

    elif action == "pop":
        stash_ref = f"stash@{{{index}}}"
        out, stderr, code = _git(["stash", "pop", stash_ref], cwd=_repo_cwd())

    elif action == "drop":
        stash_ref = f"stash@{{{index}}}"
        out, stderr, code = _git(["stash", "drop", stash_ref], cwd=_repo_cwd())

    else:
        return f"Error: unknown action '{action}'."

    if code != 0:
        return f"❌ Stash {action} failed: {stderr or out}"

    return f"✅ Stash {action}: {out.strip() or 'success'}"


# ─────────────────────────────────────────────────────────────
# REMOTE TOOLS (CODER only)
# ─────────────────────────────────────────────────────────────

@tool(args_schema=GitPushArgs)
def git_push(remote: str = "origin", branch: str = "", force: bool = False, set_upstream: bool = False) -> str:
    """Push local commits to a remote repository.

    Defaults to pushing the current branch to origin.
    Use set_upstream=True when pushing a new branch for the first time.
    Force push is available but prints a clear warning — never force-push to main/master.

    Args:
        remote: Remote name (default: 'origin').
        branch: Branch to push. Empty = current branch.
        force: Force push (rewrites remote history). Use with caution.
        set_upstream: Set tracking upstream (-u). Use for new branches.

    Returns:
        Push result with upstream tracking info.
    """
    err = _ensure_repo()
    if err:
        return err

    current = _current_branch()
    target_branch = branch or current

    # Safety: warn explicitly about force push to main/master
    if force and target_branch in ("main", "master"):
        return (
            "❌ Refused: force-push to 'main'/'master' is dangerous — it rewrites shared history.\n"
            "If you really need this, use terminal_exec with the exact git push --force command."
        )

    args = ["push"]
    if force:
        args.append("--force-with-lease")  # safer than --force: fails if remote changed
    if set_upstream:
        args.extend(["-u", remote, target_branch])
    else:
        args.extend([remote, target_branch])

    out, stderr, code = _git(args, cwd=_repo_cwd(), timeout=60)

    if code != 0:
        if "rejected" in stderr and "fetch first" in stderr:
            return (
                f"❌ Push rejected — remote has changes you don't have locally.\n"
                f"Run git_pull first to integrate remote changes, then push again.\n\n{stderr}"
            )
        return f"❌ Push failed:\n{stderr or out}"

    result = out.strip() or stderr.strip() or "Push successful"
    if set_upstream:
        result += f"\n  Tracking: {remote}/{target_branch}"
    return f"✅ Pushed '{target_branch}' → {remote}\n{result}"


@tool(args_schema=GitPullArgs)
def git_pull(remote: str = "origin", branch: str = "", rebase: bool = False) -> str:
    """Pull changes from a remote repository and merge/rebase into current branch.

    Args:
        remote: Remote to pull from (default: 'origin').
        branch: Branch to pull. Empty = current branch's upstream.
        rebase: Use rebase instead of merge for a linear history.

    Returns:
        Pull result showing files changed and commits integrated.
    """
    err = _ensure_repo()
    if err:
        return err

    args = ["pull"]
    if rebase:
        args.append("--rebase")
    args.append(remote)
    if branch:
        args.append(branch)

    out, stderr, code = _git(args, cwd=_repo_cwd(), timeout=60)

    combined = (out + "\n" + stderr).strip()
    if code != 0:
        if "conflict" in combined.lower():
            return (
                f"❌ Pull failed with merge conflicts.\n{combined}\n\n"
                "Resolve conflicts in the listed files, then:\n"
                "  1. git_add the resolved files\n"
                "  2. git_commit with a merge message"
            )
        return f"❌ Pull failed:\n{combined}"

    return f"✅ Pull complete:\n{combined}"


@tool(args_schema=GitFetchArgs)
def git_fetch(remote: str = "", prune: bool = True) -> str:
    """Fetch remote branches without merging. Safe — does not modify local files.

    Use this to see what's on the remote before deciding to pull or merge.

    Args:
        remote: Remote to fetch (empty = all remotes).
        prune: Remove stale remote-tracking branches (default True).

    Returns:
        Fetch summary showing new/updated refs.
    """
    err = _ensure_repo()
    if err:
        return err

    args = ["fetch"]
    if prune:
        args.append("--prune")
    if remote:
        args.append(remote)
    else:
        args.append("--all")

    out, stderr, code = _git(args, cwd=_repo_cwd(), timeout=60)
    combined = (out + "\n" + stderr).strip()

    if code != 0:
        return f"❌ Fetch failed:\n{combined}"

    return f"✅ Fetch complete:\n{combined or 'Already up to date.'}"


@tool(args_schema=GitMergeArgs)
def git_merge(branch: str, no_ff: bool = True, message: str = "") -> str:
    """Merge a branch into the current branch.

    Args:
        branch: Branch name (or commit ref) to merge into current branch.
        no_ff: Always create a merge commit even if fast-forward is possible.
        message: Custom merge commit message.

    Returns:
        Merge result showing files changed and any conflicts.
    """
    err = _ensure_repo()
    if err:
        return err

    current = _current_branch()
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    if message:
        args.extend(["-m", message])
    args.append(branch)

    out, stderr, code = _git(args, cwd=_repo_cwd())
    combined = (out + "\n" + stderr).strip()

    if code != 0:
        if "conflict" in combined.lower():
            # Parse conflicting files
            conflict_files = [
                line.split("CONFLICT")[1].strip().split(":")[-1].strip()
                for line in combined.splitlines()
                if "CONFLICT" in line
            ]
            files_str = "\n  - ".join(conflict_files) if conflict_files else "see output above"
            return (
                f"❌ Merge conflict while merging '{branch}' into '{current}'.\n"
                f"Conflicting files:\n  - {files_str}\n\n"
                f"Resolve conflicts in each file, then:\n"
                f"  1. git_add the resolved files\n"
                f"  2. git_commit to complete the merge\n\n"
                f"Raw output:\n{combined}"
            )
        return f"❌ Merge failed:\n{combined}"

    return f"✅ Merged '{branch}' into '{current}':\n{combined}"
