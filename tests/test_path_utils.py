"""Tests for path resolution utilities including symlink cycle detection."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


# ── _detect_symlink_cycle ────────────────────────────────────────────────────

def test_detect_symlink_cycle_no_symlink(tmp_path):
    from agent.tools.utils import _detect_symlink_cycle
    real = tmp_path / "real_file.txt"
    real.write_text("hello")
    assert _detect_symlink_cycle(str(real)) is False


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation may require admin on Windows")
def test_detect_symlink_cycle_normal_symlink(tmp_path):
    from agent.tools.utils import _detect_symlink_cycle
    target = tmp_path / "target.txt"
    target.write_text("target content")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    assert _detect_symlink_cycle(str(link)) is False


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation may require admin on Windows")
def test_detect_symlink_cycle_circular(tmp_path):
    from agent.tools.utils import _detect_symlink_cycle
    a = tmp_path / "a"
    b = tmp_path / "b"
    # a → b → a (cycle)
    a.symlink_to(b)
    b.symlink_to(a)
    assert _detect_symlink_cycle(str(a)) is True


def test_detect_symlink_cycle_nonexistent_path(tmp_path):
    from agent.tools.utils import _detect_symlink_cycle
    # Non-existent file is not a symlink
    result = _detect_symlink_cycle(str(tmp_path / "ghost.txt"))
    assert result is False


# ── resolve_path with symlink cycle detection ────────────────────────────────

def test_resolve_path_normal_file(tmp_path):
    from agent.tools.utils import resolve_path
    real = tmp_path / "file.txt"
    real.write_text("content")
    with patch("agent.tools.utils.config") as mc:
        mc.WORKSPACE_DIR = str(tmp_path)
        result = resolve_path("file.txt", workspace=str(tmp_path))
    assert result == str(real.resolve())


def test_resolve_path_rejects_traversal(tmp_path):
    from agent.tools.utils import resolve_path
    with pytest.raises(ValueError, match="outside workspace"):
        resolve_path("../../etc/passwd", workspace=str(tmp_path))


def test_resolve_path_safe_returns_none_on_traversal(tmp_path):
    from agent.tools.utils import resolve_path_safe
    result = resolve_path_safe("../../etc/passwd", workspace=str(tmp_path))
    assert result is None


def test_resolve_tool_path_clamps_on_traversal(tmp_path):
    from agent.tools.utils import resolve_tool_path
    result = resolve_tool_path("../../etc/passwd", workspace=str(tmp_path))
    ws = os.path.realpath(str(tmp_path))
    # Must be within or equal to workspace
    assert result.startswith(ws) or result == ws


def test_resolve_path_absolute_within_workspace(tmp_path):
    from agent.tools.utils import resolve_path
    sub = tmp_path / "sub" / "file.py"
    sub.parent.mkdir()
    sub.write_text("code")
    result = resolve_path(str(sub), workspace=str(tmp_path))
    assert result == str(sub.resolve())


def test_resolve_path_absolute_outside_workspace_rejected(tmp_path):
    from agent.tools.utils import resolve_path
    with pytest.raises(ValueError):
        resolve_path("/etc/hosts", workspace=str(tmp_path))
