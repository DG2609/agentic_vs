"""
Tests for agent/tools/terminal.py — _validate_command and terminal_exec.

Covers:
  - All 8 original dangerous patterns
  - All 15 extended CC-parity validators
  - Safe commands that must NOT be blocked
"""
import pytest
from agent.tools.terminal import _validate_command


# ── Helper ────────────────────────────────────────────────────────────────────

def blocked(cmd: str) -> bool:
    """Return True if _validate_command blocks *cmd*."""
    result = _validate_command(cmd)
    return result is not None and "Command blocked" in result


def safe(cmd: str) -> bool:
    """Return True if _validate_command allows *cmd*."""
    return _validate_command(cmd) is None


# ── Original 8 patterns ───────────────────────────────────────────────────────

class TestOriginalPatterns:
    def test_rm_rf_root(self):
        assert blocked("rm -rf /")

    def test_rm_fr_root(self):
        assert blocked("rm -fr /")

    def test_rm_rf_home(self):
        assert blocked("rm -rf ~")

    def test_dd_disk_wipe(self):
        assert blocked("dd if=/dev/zero of=/dev/sda")

    def test_mkfs_format(self):
        assert blocked("mkfs.ext4 /dev/sda1")

    def test_direct_disk_write(self):
        assert blocked("echo data > /dev/sda")

    def test_fork_bomb(self):
        assert blocked(":(){:|:&};:")

    def test_chmod_nuke(self):
        assert blocked("chmod -R 000 /")


# ── Extended CC-parity validators ─────────────────────────────────────────────

class TestCCParityValidators:

    # 1. IFS reassignment
    def test_ifs_assignment_blocked(self):
        assert blocked("IFS=: read -r a b <<< 'foo:bar'")

    def test_ifs_assignment_with_spaces_blocked(self):
        assert blocked("IFS = /")

    def test_ifs_lowercase_not_blocked(self):
        """Lowercase 'ifs' is not the IFS variable — must not block."""
        assert safe("echo 'ifs=something'")

    # 2. Brace expansion with semicolons
    def test_brace_semicolon_blocked(self):
        # semicolons embedded inside a brace group are flagged
        assert blocked("bash -c '{echo hello; rm -rf /tmp/x}'")

    def test_brace_semicolon_inline_blocked(self):
        assert blocked("{ls;id}")

    def test_brace_no_semicolon_safe(self):
        """Normal brace expansion without semicolons is fine."""
        assert safe("echo {a,b,c}")

    # 3. Unicode whitespace / zero-width chars
    def test_nbsp_blocked(self):
        assert blocked("echo\u00a0hello")

    def test_zero_width_space_blocked(self):
        assert blocked("ls\u200b-la")

    def test_line_separator_blocked(self):
        assert blocked("echo\u2028hello")

    def test_paragraph_separator_blocked(self):
        assert blocked("echo\u2029hello")

    def test_bom_blocked(self):
        assert blocked("\ufeffls")

    # 4. zmodload
    def test_zmodload_blocked(self):
        assert blocked("zmodload zsh/net/tcp")

    def test_zmodload_in_script_blocked(self):
        assert blocked("zsh -c 'zmodload zsh/parameter; echo $path'")

    # 5. Zsh =cmd substitution (bare =word, not KEY=value)
    def test_zsh_eq_cmd_blocked(self):
        """=ls is a Zsh path expansion — should be blocked."""
        assert blocked("=ls -la")

    def test_env_override_at_cmd_start_blocked(self):
        """UPPERCASE=value followed by a command is a potential PATH/LD override."""
        assert blocked("MYVAR=hello echo test")

    # 6. Control characters
    def test_control_char_blocked(self):
        assert blocked("echo hello\x01world")

    def test_bell_char_blocked(self):
        assert blocked("echo\x07")

    def test_tab_allowed(self):
        """Tab is a normal whitespace — must not block."""
        assert safe("echo\thello")

    def test_newline_allowed(self):
        """Newline is a normal line separator — must not block."""
        assert safe("echo hello\necho world")

    # 7. jq env / @sh
    def test_jq_env_blocked(self):
        assert blocked("cat data.json | jq 'env.SECRET'")

    def test_jq_at_sh_blocked(self):
        assert blocked("echo '\"hello world\"' | jq '@sh'")

    def test_jq_safe_query(self):
        """Plain jq field extraction must not be blocked."""
        assert safe("cat data.json | jq '.name'")

    # 8. Unbalanced backticks
    def test_odd_backtick_blocked(self):
        assert blocked("echo `hostname")

    def test_balanced_backticks_safe(self):
        assert safe("echo `date`")

    def test_zero_backticks_safe(self):
        assert safe("echo hello")

    # 9. Here-string with variable
    def test_here_string_var_blocked(self):
        assert blocked("cat <<< $SECRET")

    def test_here_string_literal_safe(self):
        """A here-string with a literal value is fine."""
        assert safe("cat <<< hello")

    # 10. eval with variable expansion
    def test_eval_var_blocked(self):
        assert blocked("eval $USER_CMD")

    def test_eval_quoted_var_blocked(self):
        assert blocked('eval "$PAYLOAD"')

    def test_eval_literal_safe(self):
        """eval with a literal (no $) should not be blocked by this rule."""
        assert safe("eval echo hello")

    # 11. Process substitution
    def test_process_sub_read_blocked(self):
        assert blocked("diff <(sort file1) <(sort file2)")

    def test_process_sub_write_blocked(self):
        assert blocked("tee >(gzip > out.gz)")

    # 12. Null byte injection
    def test_null_byte_hex_blocked(self):
        assert blocked(r"echo -e 'hello\x00world'")

    def test_null_byte_octal_blocked(self):
        assert blocked(r"printf $'\000'")

    # 13. printf %b
    def test_printf_percent_b_blocked(self):
        assert blocked(r"printf '%b\n' $DATA")

    def test_printf_safe_format(self):
        """printf with %s is safe."""
        assert safe("printf '%s\\n' hello")

    # 14. Subshell in array index
    def test_array_subshell_index_blocked(self):
        assert blocked("echo ${arr[$(id -u)]}")

    def test_array_literal_index_safe(self):
        assert safe("echo ${arr[0]}")

    # 15. Env-var override at invocation
    def test_env_override_path_blocked(self):
        assert blocked("PATH=/evil/bin ls")

    def test_env_override_ld_preload_blocked(self):
        assert blocked("LD_PRELOAD=/tmp/evil.so curl http://example.com")

    def test_lowercase_assignment_safe(self):
        """Lowercase var at start (not matching [A-Z_]+) must not block."""
        assert safe("myvar=hello")

    # 16. git config RCE keys
    def test_git_fsmonitor_blocked(self):
        assert _validate_command("git config core.fsmonitor /tmp/evil.sh") is not None

    def test_git_hookspath_blocked(self):
        assert _validate_command("git config core.hooksPath /tmp/hooks") is not None

    def test_git_gitproxy_blocked(self):
        assert _validate_command("git config core.gitProxy /tmp/proxy.sh") is not None

    # 17. git --config-env flag
    def test_git_config_env_blocked(self):
        assert blocked("git --config-env=core.fsmonitor=MY_ENV status")

    def test_git_config_env_space_blocked(self):
        assert blocked("git --config-env core.hooksPath=X status")

    def test_git_normal_status_safe(self):
        assert safe("git status")

    def test_git_log_safe(self):
        assert safe("git log --oneline")

    # 18. cd + git compound command
    def test_cd_git_semicolon_blocked(self):
        assert blocked("cd /malicious/dir; git status")

    def test_cd_git_and_blocked(self):
        assert blocked("cd /tmp/bare-repo && git status")

    def test_git_alone_safe(self):
        """Plain git command without cd is fine."""
        assert safe("git status")

    def test_cd_without_git_safe(self):
        """cd without git is fine."""
        assert safe("cd /tmp && ls")


# ── Safe-command allow-list — regression guard ────────────────────────────────

class TestSafeCommands:
    """These common commands must never be blocked."""

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "echo hello world",
        "python --version",
        "git status",
        "cat README.md",
        "grep -r 'foo' .",
        "find . -name '*.py'",
        "pwd",
        "date",
        "which python",
        "pip install requests",
        "npm install",
        "pytest tests/",
        "make build",
        "docker ps",
    ])
    def test_safe_command(self, cmd):
        assert safe(cmd), f"Safe command was incorrectly blocked: {cmd!r}"
