"""Tests for the agent_summary tool."""
import pytest
from unittest.mock import patch, MagicMock


def test_agent_summary_returns_string():
    from agent.tools.agent_summary import agent_summary
    result = agent_summary.invoke({})
    assert isinstance(result, str)
    assert len(result) > 0


def test_agent_summary_includes_workspace():
    from agent.tools.agent_summary import agent_summary
    import config
    result = agent_summary.invoke({})
    assert config.WORKSPACE_DIR in result or "Workspace" in result


def test_agent_summary_includes_provider():
    from agent.tools.agent_summary import agent_summary
    import config
    result = agent_summary.invoke({})
    assert config.LLM_PROVIDER in result or "Provider" in result


def test_agent_summary_includes_git_when_enabled(tmp_path):
    from agent.tools.agent_summary import agent_summary
    import subprocess
    # Init a real git repo for testing
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "f.txt").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

    with patch("agent.tools.agent_summary.config") as mc:
        mc.WORKSPACE_DIR = str(tmp_path)
        mc.LLM_PROVIDER = "anthropic"
        mc.LLM_MODEL = "test-model"
        mc.ADVISOR_MODEL = ""
        mc.UNDERCOVER_MODE = False
        mc.COORDINATOR_MODE = False
        mc.AUTO_DREAM_ENABLED = True
        result = agent_summary.invoke({"include_git": True})
    assert "Git" in result or "branch" in result.lower()


def test_agent_summary_skips_git_when_disabled():
    from agent.tools.agent_summary import agent_summary
    with patch("agent.tools.agent_summary.config") as mc:
        mc.WORKSPACE_DIR = "."
        mc.LLM_PROVIDER = "test"
        mc.LLM_MODEL = "model"
        mc.ADVISOR_MODEL = ""
        mc.UNDERCOVER_MODE = False
        mc.COORDINATOR_MODE = False
        mc.AUTO_DREAM_ENABLED = True
        result = agent_summary.invoke({"include_git": False})
    assert "Git branch" not in result


def test_agent_summary_includes_todos_when_present():
    from agent.tools.agent_summary import agent_summary
    from agent.tools.todo import _set_todos
    _set_todos([{"id": 1, "content": "my special task", "status": "pending"}])
    with patch("agent.tools.agent_summary.config") as mc:
        mc.WORKSPACE_DIR = "."
        mc.LLM_PROVIDER = "test"
        mc.LLM_MODEL = "m"
        mc.ADVISOR_MODEL = ""
        mc.UNDERCOVER_MODE = False
        mc.COORDINATOR_MODE = False
        mc.AUTO_DREAM_ENABLED = True
        result = agent_summary.invoke({"include_todos": True})
    _set_todos([])  # cleanup
    assert "my special task" in result


def test_agent_summary_skips_todos_when_disabled():
    from agent.tools.agent_summary import agent_summary
    from agent.tools.todo import _set_todos
    _set_todos([{"id": 1, "content": "hidden todo", "status": "pending"}])
    with patch("agent.tools.agent_summary.config") as mc:
        mc.WORKSPACE_DIR = "."
        mc.LLM_PROVIDER = "test"
        mc.LLM_MODEL = "m"
        mc.ADVISOR_MODEL = ""
        mc.UNDERCOVER_MODE = False
        mc.COORDINATOR_MODE = False
        mc.AUTO_DREAM_ENABLED = True
        result = agent_summary.invoke({"include_todos": False})
    _set_todos([])  # cleanup
    assert "hidden todo" not in result


def test_agent_summary_shows_config_snapshot():
    from agent.tools.agent_summary import agent_summary
    with patch("agent.tools.agent_summary.config") as mc:
        mc.WORKSPACE_DIR = "."
        mc.LLM_PROVIDER = "test"
        mc.LLM_MODEL = "m"
        mc.ADVISOR_MODEL = "claude-opus-4-6"
        mc.UNDERCOVER_MODE = True
        mc.COORDINATOR_MODE = False
        mc.AUTO_DREAM_ENABLED = False
        result = agent_summary.invoke({})
    assert "claude-opus-4-6" in result
    assert "True" in result  # undercover
