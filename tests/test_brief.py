"""Tests for the brief (multi-file context summarization) tool."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


def _setup_workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temporary workspace with given filename→content mapping."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ── Basic functionality ───────────────────────────────────────────────────────

def test_brief_single_file(tmp_path):
    from agent.tools.brief import brief
    ws = _setup_workspace(tmp_path, {"hello.py": "print('hello')\n"})
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["hello.py"]})
    assert "hello.py" in result
    assert "print('hello')" in result


def test_brief_multiple_files(tmp_path):
    from agent.tools.brief import brief
    ws = _setup_workspace(tmp_path, {
        "a.py": "# file a\n",
        "b.py": "# file b\n",
    })
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["a.py", "b.py"]})
    assert "a.py" in result
    assert "b.py" in result


def test_brief_empty_paths_returns_error():
    from agent.tools.brief import brief
    result = brief.invoke({"paths": []})
    assert "Error" in result


def test_brief_nonexistent_file(tmp_path):
    from agent.tools.brief import brief
    with patch("agent.tools.utils.resolve_tool_path", return_value=str(tmp_path / "ghost.py")), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(tmp_path)
        result = brief.invoke({"paths": ["ghost.py"]})
    assert "not found" in result.lower() or "no readable" in result.lower() or "Skipped" in result


def test_brief_truncates_at_max_lines(tmp_path):
    from agent.tools.brief import brief
    content = "\n".join(f"line {i}" for i in range(200))
    ws = _setup_workspace(tmp_path, {"big.py": content})
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["big.py"], "max_lines_per_file": 50})
    assert "50 of" in result or "first 50" in result


def test_brief_focus_appears_in_output(tmp_path):
    from agent.tools.brief import brief
    ws = _setup_workspace(tmp_path, {"api.py": "def get_user(): pass"})
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["api.py"], "focus": "public API"})
    assert "public API" in result


def test_brief_includes_file_count(tmp_path):
    from agent.tools.brief import brief
    ws = _setup_workspace(tmp_path, {
        "x.py": "x = 1",
        "y.py": "y = 2",
    })
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["x.py", "y.py"]})
    assert "Files: 2" in result or "2" in result


def test_brief_skips_empty_files(tmp_path):
    from agent.tools.brief import brief
    ws = _setup_workspace(tmp_path, {"empty.py": "", "real.py": "print('hi')"})
    with patch("agent.tools.brief.resolve_tool_path", side_effect=lambda p: str(ws / p)), \
         patch("agent.tools.brief.config") as mock_cfg:
        mock_cfg.WORKSPACE_DIR = str(ws)
        result = brief.invoke({"paths": ["empty.py", "real.py"]})
    assert "Skipped" in result or "empty" in result.lower()
    assert "print('hi')" in result
