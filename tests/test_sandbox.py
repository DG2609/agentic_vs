"""Tests for agent/sandbox.py (3C-3 Container Sandbox)."""
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

import config


# ── _check_docker ──────────────────────────────────────────────

def test_check_docker_not_found():
    """If docker binary is missing, _check_docker returns False."""
    from agent.sandbox import _check_docker
    with patch("shutil.which", return_value=None):
        assert _check_docker() is False


def test_check_docker_daemon_not_running():
    """If docker info returns non-zero, _check_docker returns False."""
    from agent.sandbox import _check_docker
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert _check_docker() is False


def test_check_docker_available():
    """If docker info returns 0, _check_docker returns True."""
    from agent.sandbox import _check_docker
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert _check_docker() is True


def test_check_docker_timeout_returns_false():
    """If docker info times out, _check_docker returns False (no crash)."""
    from agent.sandbox import _check_docker
    with patch("shutil.which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            assert _check_docker() is False


# ── sandbox_exec ──────────────────────────────────────────────

def _mock_run_ok(stdout="hello", returncode=0):
    """Return a mock subprocess result."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def test_sandbox_exec_success():
    """Successful command returns formatted output with [sandbox] tag."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", return_value=_mock_run_ok("hello world")) as mock_run:
        result = sandbox_exec("echo hello", "/workspace", timeout=10)

    assert "✅ [sandbox]" in result
    assert "hello world" in result


def test_sandbox_exec_failure_exit_code():
    """Non-zero exit code shows ❌ [sandbox]."""
    from agent.sandbox import sandbox_exec
    m = MagicMock(returncode=1, stdout="", stderr="command not found")
    with patch("subprocess.run", return_value=m):
        result = sandbox_exec("notacommand", "/workspace", timeout=10)

    assert "❌ [sandbox]" in result
    assert "command not found" in result


def test_sandbox_exec_stderr_in_output():
    """Stderr is included in the output under [STDERR] label."""
    from agent.sandbox import sandbox_exec
    m = MagicMock(returncode=0, stdout="ok", stderr="warning: something")
    with patch("subprocess.run", return_value=m):
        result = sandbox_exec("cmd", "/workspace", timeout=10)

    assert "[STDERR]" in result
    assert "warning: something" in result


def test_sandbox_exec_no_output():
    """Command with no stdout/stderr shows (no output)."""
    from agent.sandbox import sandbox_exec
    m = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=m):
        result = sandbox_exec("true", "/workspace", timeout=10)

    assert "(no output)" in result


def test_sandbox_exec_timeout_kills_container():
    """On timeout, sandbox_exec calls docker stop/rm and returns timeout message."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 10)) as mock_run:
        result = sandbox_exec("sleep 99", "/workspace", timeout=10)

    assert "timed out" in result
    assert "10s" in result
    # Should have attempted to stop and rm the container
    assert mock_run.call_count >= 2  # original run + stop + rm


def test_sandbox_exec_uses_correct_docker_args():
    """sandbox_exec passes expected Docker flags."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", return_value=_mock_run_ok()) as mock_run:
        sandbox_exec("ls", "/my/workspace", timeout=30)

    args = mock_run.call_args[0][0]  # first positional arg (the list)
    assert "docker" in args[0]
    assert "run" in args
    assert "--rm" in args
    assert "--network" in args
    assert "/my/workspace:/my/workspace:rw" in args or any(
        "/my/workspace" in a for a in args
    )
    assert "-w" in args
    assert "/my/workspace" in args
    assert "sh" in args
    assert "-c" in args
    assert "ls" in args


def test_sandbox_exec_readonly_mount():
    """SANDBOX_READONLY=True uses :ro mount."""
    from agent.sandbox import sandbox_exec
    original = config.SANDBOX_READONLY
    try:
        config.SANDBOX_READONLY = True
        with patch("subprocess.run", return_value=_mock_run_ok()) as mock_run:
            sandbox_exec("ls", "/ws", timeout=10)
        args = mock_run.call_args[0][0]
        # The volume spec should end in :ro
        volume_arg = next((a for a in args if "/ws:/ws" in a), "")
        assert volume_arg.endswith(":ro")
    finally:
        config.SANDBOX_READONLY = original


def test_sandbox_exec_subprocess_timeout_includes_overhead():
    """Subprocess timeout = specified timeout + 10 (Docker startup overhead)."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", return_value=_mock_run_ok()) as mock_run:
        sandbox_exec("cmd", "/ws", timeout=30)
    _, kwargs = mock_run.call_args
    assert kwargs.get("timeout") == 40  # 30 + 10


def test_sandbox_exec_docker_not_found():
    """FileNotFoundError from Docker returns helpful error string."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", side_effect=FileNotFoundError("docker: no such file")):
        result = sandbox_exec("ls", "/ws", timeout=10)
    assert "docker executable not found" in result


def test_sandbox_exec_unexpected_exception():
    """Unexpected exception returns error string, not crash."""
    from agent.sandbox import sandbox_exec
    with patch("subprocess.run", side_effect=OSError("permission denied")):
        result = sandbox_exec("ls", "/ws", timeout=10)
    assert "Error in sandbox execution" in result


# ── pull_sandbox_image ────────────────────────────────────────

def test_pull_image_success():
    """Successful docker pull returns True."""
    from agent.sandbox import pull_sandbox_image
    with patch("agent.sandbox.SANDBOX_AVAILABLE", True):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert pull_sandbox_image() is True


def test_pull_image_failure():
    """Failed docker pull returns False."""
    from agent.sandbox import pull_sandbox_image
    with patch("agent.sandbox.SANDBOX_AVAILABLE", True):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="image not found")
            assert pull_sandbox_image() is False


def test_pull_image_when_unavailable():
    """If sandbox unavailable, pull returns False immediately."""
    from agent.sandbox import pull_sandbox_image
    with patch("agent.sandbox.SANDBOX_AVAILABLE", False):
        assert pull_sandbox_image() is False


# ── terminal_exec integration ──────────────────────────────────

def test_terminal_uses_sandbox_when_enabled():
    """When SANDBOX_ENABLED=True and Docker available, sandbox_exec is called."""
    original = config.SANDBOX_ENABLED
    try:
        config.SANDBOX_ENABLED = True
        with patch("agent.sandbox.SANDBOX_AVAILABLE", True):
            with patch("agent.sandbox.sandbox_exec", return_value="Exit code: 0 ✅ [sandbox]\n\nhello") as mock_sandbox:
                from agent.tools.terminal import terminal_exec
                result = terminal_exec.invoke({"command": "echo hello", "cwd": "", "timeout": 0})
        mock_sandbox.assert_called_once()
        assert "[sandbox]" in result
    finally:
        config.SANDBOX_ENABLED = original


def test_terminal_falls_back_when_docker_unavailable():
    """When SANDBOX_ENABLED=True but Docker unavailable, falls back to direct exec."""
    original = config.SANDBOX_ENABLED
    try:
        config.SANDBOX_ENABLED = True
        with patch("agent.sandbox.SANDBOX_AVAILABLE", False):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="direct", stderr="")
                from agent.tools.terminal import terminal_exec
                result = terminal_exec.invoke({"command": "echo direct", "cwd": "", "timeout": 0})
        # Should NOT have [sandbox] tag — ran directly
        assert "[sandbox]" not in result
    finally:
        config.SANDBOX_ENABLED = original


def test_terminal_direct_when_disabled():
    """When SANDBOX_ENABLED=False, always uses direct subprocess (via Popen)."""
    original = config.SANDBOX_ENABLED
    try:
        config.SANDBOX_ENABLED = False
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("direct output", "")
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            from agent.tools.terminal import terminal_exec
            result = terminal_exec.invoke({"command": "echo direct", "cwd": "", "timeout": 0})
        assert "direct output" in result
        assert "[sandbox]" not in result
    finally:
        config.SANDBOX_ENABLED = original


# ── Config schema ──────────────────────────────────────────────

def test_sandbox_config_defaults():
    """Default sandbox config values are sane."""
    assert config.SANDBOX_ENABLED is False
    assert config.SANDBOX_IMAGE == "python:3.12-slim"
    assert config.SANDBOX_NETWORK == "none"
    assert config.SANDBOX_MEMORY == "512m"
    assert config.SANDBOX_CPUS == "1.0"
    assert config.SANDBOX_PIDS_LIMIT == 100
    assert config.SANDBOX_READONLY is False
