"""Tests for terminal security hardening: sed -i, destructive warnings."""
import pytest


# ── _check_sed_safety ────────────────────────────────────────────────────────

def test_sed_i_no_backup_warns():
    from agent.tools.terminal import _check_sed_safety
    result = _check_sed_safety("sed -i 's/foo/bar/' file.txt")
    assert result is not None
    assert "portable" in result.lower() or "macOS" in result or "Warning" in result


def test_sed_i_with_empty_backup_is_safe():
    from agent.tools.terminal import _check_sed_safety
    # BSD/macOS portable form
    result = _check_sed_safety("sed -i '' 's/foo/bar/' file.txt")
    assert result is None


def test_sed_unrelated_to_i_is_safe():
    from agent.tools.terminal import _check_sed_safety
    result = _check_sed_safety("sed 's/foo/bar/' file.txt")
    assert result is None


def test_sed_i_double_quoted_backup_is_safe():
    from agent.tools.terminal import _check_sed_safety
    result = _check_sed_safety('sed -i "" "s/foo/bar/" file.txt')
    assert result is None


# ── _check_destructive ───────────────────────────────────────────────────────

def test_destructive_rm_rf_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("rm -rf ./old_dir")
    assert result is not None
    assert "Destructive" in result or "warning" in result.lower()


def test_git_reset_hard_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("git reset --hard HEAD~1")
    assert result is not None


def test_git_push_force_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("git push origin main --force")
    assert result is not None


def test_git_clean_f_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("git clean -fd")
    assert result is not None


def test_safe_command_no_warning():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("ls -la")
    assert result is None


def test_echo_no_warning():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("echo hello world")
    assert result is None


def test_drop_table_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("psql -c 'DROP TABLE users'")
    assert result is not None


def test_truncate_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("truncate -s 0 logfile.log")
    assert result is not None


def test_shred_warns():
    from agent.tools.terminal import _check_destructive
    result = _check_destructive("shred -u secret.key")
    assert result is not None


# ── Integration: warning prepended to output ─────────────────────────────────

def test_terminal_exec_prepends_destructive_warning(tmp_path):
    """Destructive warning is prepended to the output, not blocking."""
    from agent.tools.terminal import terminal_exec
    from unittest.mock import patch
    import config

    with patch.object(config, "SANDBOX_ENABLED", False), \
         patch.object(config, "WORKSPACE_DIR", str(tmp_path)), \
         patch.object(config, "TOOL_TIMEOUT", 10):
        result = terminal_exec.invoke({
            "command": "git reset --hard HEAD",
            "cwd": str(tmp_path),
        })

    # Warning should be in the output (even if git fails due to no repo)
    assert "warning" in result.lower() or "Destructive" in result
