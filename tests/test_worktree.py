"""Tests for agent/worktree.py — git worktree isolation."""
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_git_repo(tmp_path):
    """Create a real temporary git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), capture_output=True)
    return str(repo)


class TestCreateWorktree:
    def test_create_worktree(self, mock_git_repo, tmp_path):
        from agent.worktree import create_worktree, _WORKTREE_BASE
        wt_base = tmp_path / "wt"
        with patch("agent.worktree._WORKTREE_BASE", wt_base), \
             patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            mc.DATA_DIR = str(tmp_path / "data")
            wt = create_worktree(workspace=mock_git_repo)
        assert os.path.isdir(wt.path)
        assert wt.branch.startswith("shadowdev-wt-")
        assert os.path.isfile(os.path.join(wt.path, "README.md"))

    def test_create_with_custom_branch(self, mock_git_repo, tmp_path):
        from agent.worktree import create_worktree
        wt_base = tmp_path / "wt"
        with patch("agent.worktree._WORKTREE_BASE", wt_base), \
             patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            mc.DATA_DIR = str(tmp_path / "data")
            wt = create_worktree(branch_name="feature-test", workspace=mock_git_repo)
        assert wt.branch == "feature-test"

    def test_create_not_git_repo_raises(self, tmp_path):
        from agent.worktree import create_worktree
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        with patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = str(non_repo)
            with pytest.raises(RuntimeError, match="Not in a git repository"):
                create_worktree(workspace=str(non_repo))


class TestListWorktrees:
    def test_list_includes_main(self, mock_git_repo):
        from agent.worktree import list_worktrees
        with patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            wts = list_worktrees(workspace=mock_git_repo)
        assert len(wts) >= 1  # At least the main worktree

    def test_list_after_create(self, mock_git_repo, tmp_path):
        from agent.worktree import create_worktree, list_worktrees
        wt_base = tmp_path / "wt"
        with patch("agent.worktree._WORKTREE_BASE", wt_base), \
             patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            mc.DATA_DIR = str(tmp_path / "data")
            create_worktree(workspace=mock_git_repo)
            wts = list_worktrees(workspace=mock_git_repo)
        assert len(wts) >= 2  # main + new worktree


class TestCleanupWorktree:
    def test_cleanup(self, mock_git_repo, tmp_path):
        from agent.worktree import create_worktree, cleanup_worktree
        wt_base = tmp_path / "wt"
        with patch("agent.worktree._WORKTREE_BASE", wt_base), \
             patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            mc.DATA_DIR = str(tmp_path / "data")
            wt = create_worktree(workspace=mock_git_repo)
            assert os.path.isdir(wt.path)
            cleanup_worktree(wt)
        assert not os.path.isdir(wt.path)


class TestMergeWorktree:
    def test_merge(self, mock_git_repo, tmp_path):
        from agent.worktree import create_worktree, merge_worktree, cleanup_worktree
        wt_base = tmp_path / "wt"
        with patch("agent.worktree._WORKTREE_BASE", wt_base), \
             patch("agent.worktree.config") as mc:
            mc.WORKSPACE_DIR = mock_git_repo
            mc.DATA_DIR = str(tmp_path / "data")
            wt = create_worktree(workspace=mock_git_repo)
            # Make a change in worktree
            new_file = os.path.join(wt.path, "feature.py")
            with open(new_file, "w") as f:
                f.write("# new feature\n")
            subprocess.run(["git", "add", "."], cwd=wt.path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add feature"], cwd=wt.path, capture_output=True)
            # Merge back
            result = merge_worktree(wt)
            assert "Merged" in result or "conflict" in result.lower()
            cleanup_worktree(wt, delete_branch=True)
