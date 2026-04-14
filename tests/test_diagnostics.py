"""Tests for the diagnostics tool."""
import pytest
from unittest.mock import patch


def test_diagnostics_returns_string():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert isinstance(result, str)
    assert len(result) > 0


def test_diagnostics_contains_header():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "Diagnostics" in result


def test_diagnostics_shows_python_version():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "Python version" in result


def test_diagnostics_shows_pass_fail_counts():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "passed" in result


def test_diagnostics_shows_workspace():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "Workspace" in result


def test_diagnostics_shows_git_check():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "Git" in result


def test_diagnostics_includes_required_packages():
    from agent.tools.diagnostics import diagnostics
    result = diagnostics.invoke({})
    assert "langchain_core" in result


def test_diagnostics_check_helper():
    from agent.tools.diagnostics import _check
    r = _check("test label", True, "detail")
    assert r["label"] == "test label"
    assert r["ok"] is True
    assert r["detail"] == "detail"


def test_diagnostics_check_fail():
    from agent.tools.diagnostics import _check
    r = _check("fail", False, "reason")
    assert r["ok"] is False


def test_diagnostics_check_warn():
    from agent.tools.diagnostics import _check
    r = _check("warn", None, "optional")
    assert r["ok"] is None


def test_diagnostics_reports_missing_api_key():
    from agent.tools.diagnostics import diagnostics
    with patch("agent.tools.diagnostics.config") as mc:
        mc.LLM_PROVIDER = "anthropic"
        mc.ANTHROPIC_API_KEY = ""
        mc.WORKSPACE_DIR = "."
        mc.MCP_SERVERS = {}
        mc.SANDBOX_ENABLED = False
        result = diagnostics.invoke({})
    assert "MISSING" in result or "API key" in result


def test_diagnostics_shows_all_checks_passed_when_ok(tmp_path):
    from agent.tools.diagnostics import diagnostics
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    with patch("agent.tools.diagnostics.config") as mc:
        mc.LLM_PROVIDER = "anthropic"
        mc.ANTHROPIC_API_KEY = "sk-test-key"
        mc.WORKSPACE_DIR = str(tmp_path)
        mc.MCP_SERVERS = {}
        mc.SANDBOX_ENABLED = False
        result = diagnostics.invoke({})
    assert "passed" in result
