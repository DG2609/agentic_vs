"""
Git worktree isolation — create isolated working copies for parallel agent work.

Each worktree is a separate checkout of the repository at a given ref,
allowing multiple agents to work on different branches simultaneously
without interfering with each other.

Usage:
    wt = create_worktree("feature-x")  → creates isolated copy
    ... agent works in wt.path ...
    cleanup_worktree(wt)                → removes worktree + branch
"""

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4

import config

logger = logging.getLogger(__name__)

_WORKTREE_BASE = Path(config.DATA_DIR) / "worktrees"


@dataclass
class Worktree:
    """Represents an active git worktree."""
    id: str
    path: str               # Absolute path to worktree directory
    branch: str             # Branch name
    base_ref: str           # The ref this was branched from
    repo_root: str          # Original repo root


def _git(args: list[str], cwd: str = "", timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        capture_output=True, text=True,
        cwd=cwd or str(config.WORKSPACE_DIR),
        timeout=timeout,
    )


def _get_repo_root(workspace: str = "") -> Optional[str]:
    """Get the git repo root for the workspace."""
    result = _git(["rev-parse", "--show-toplevel"], cwd=workspace or str(config.WORKSPACE_DIR))
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def create_worktree(
    branch_name: str = "",
    base_ref: str = "HEAD",
    workspace: str = "",
) -> Worktree:
    """Create a new git worktree for isolated agent work.

    Args:
        branch_name: Name for the new branch. Auto-generated if empty.
        base_ref: Git ref to base the worktree on (default: HEAD).
        workspace: Workspace directory (default: config.WORKSPACE_DIR).

    Returns:
        Worktree object with path to the isolated directory.

    Raises:
        RuntimeError: If worktree creation fails.
    """
    workspace = workspace or str(config.WORKSPACE_DIR)
    repo_root = _get_repo_root(workspace)
    if not repo_root:
        raise RuntimeError("Not in a git repository")

    wt_id = uuid4().hex[:8]
    if not branch_name:
        branch_name = f"shadowdev-wt-{wt_id}"

    _WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
    wt_path = str(_WORKTREE_BASE / wt_id)

    # Create worktree with new branch
    result = _git(
        ["worktree", "add", "-b", branch_name, wt_path, base_ref],
        cwd=repo_root,
    )
    if result.returncode != 0:
        # Try without -b if branch already exists
        result = _git(
            ["worktree", "add", wt_path, branch_name],
            cwd=repo_root,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create worktree: {result.stderr.strip()}")

    wt = Worktree(
        id=wt_id,
        path=wt_path,
        branch=branch_name,
        base_ref=base_ref,
        repo_root=repo_root,
    )
    logger.info("[worktree] Created %s at %s (branch: %s)", wt_id, wt_path, branch_name)
    return wt


def list_worktrees(workspace: str = "") -> list[dict]:
    """List all active git worktrees."""
    workspace = workspace or str(config.WORKSPACE_DIR)
    repo_root = _get_repo_root(workspace)
    if not repo_root:
        return []

    result = _git(["worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        return []

    worktrees = []
    current = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
    if current:
        worktrees.append(current)

    return worktrees


def cleanup_worktree(wt: Worktree, delete_branch: bool = True) -> bool:
    """Remove a worktree and optionally its branch.

    Args:
        wt: The worktree to clean up.
        delete_branch: Also delete the associated branch (default: True).

    Returns:
        True if cleanup succeeded.
    """
    # Remove the worktree
    result = _git(["worktree", "remove", "--force", wt.path], cwd=wt.repo_root)
    if result.returncode != 0:
        # Fallback: manual removal
        if os.path.exists(wt.path):
            shutil.rmtree(wt.path, ignore_errors=True)
        _git(["worktree", "prune"], cwd=wt.repo_root)

    # Delete the branch if requested
    if delete_branch and wt.branch:
        _git(["branch", "-D", wt.branch], cwd=wt.repo_root)

    logger.info("[worktree] Cleaned up %s (branch: %s)", wt.id, wt.branch)
    return True


def merge_worktree(wt: Worktree, target_branch: str = "") -> str:
    """Merge worktree changes back to a target branch.

    Args:
        wt: The worktree with changes.
        target_branch: Branch to merge into (default: current branch of main repo).

    Returns:
        Merge result message.
    """
    if not target_branch:
        result = _git(["branch", "--show-current"], cwd=wt.repo_root)
        target_branch = result.stdout.strip() or "main"

    # Merge the worktree branch into target
    result = _git(["merge", wt.branch, "--no-ff", "-m", f"Merge worktree {wt.id}: {wt.branch}"], cwd=wt.repo_root)
    if result.returncode != 0:
        return f"Merge conflict: {result.stderr.strip()}"

    return f"Merged {wt.branch} into {target_branch}"


def cleanup_stale_worktrees() -> int:
    """Remove any stale/broken worktree references."""
    workspace = str(config.WORKSPACE_DIR)
    repo_root = _get_repo_root(workspace)
    if not repo_root:
        return 0
    result = _git(["worktree", "prune"], cwd=repo_root)
    return 0 if result.returncode == 0 else -1
