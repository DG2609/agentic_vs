"""
Tool: terminal_exec — run shell commands with timeout and output capture.
All outputs go through universal truncation.

When config.SANDBOX_ENABLED=True and Docker is available, commands run inside
an isolated container (network isolation, memory/CPU limits, no privilege escalation).
Falls back to direct execution if Docker is unavailable.
"""
import logging
import os
import re
import subprocess
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_path_safe
from models.tool_schemas import TerminalExecArgs

logger = logging.getLogger(__name__)

# ── Binary-hijacking env var scrub ───────────────────────────
# These vars can redirect shared-library / interpreter loading and allow
# an attacker to intercept any subprocess we spawn.

_HIJACK_ENV_VARS = {
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "PYTHONPATH", "RUBYOPT", "NODE_OPTIONS",
    "PERL5LIB", "PERLLIB",
}


def _safe_env() -> dict:
    """Return os.environ copy with binary-hijacking vars removed."""
    env = os.environ.copy()
    for var in _HIJACK_ENV_VARS:
        env.pop(var, None)
    return env


# ── Pre-compiled regex patterns (compiled once at module load) ───────────────
# Original 8 patterns — applied to lowercased, whitespace-normalised command
_DENIED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"rm\s+(-\w*\s+)*-\w*r\w*f\w*\s+/"),  "rm -rf /"),
    (re.compile(r"rm\s+(-\w*\s+)*-\w*f\w*r\w*\s+/"),  "rm -rf /"),
    (re.compile(r"rm\s+(-\w*\s+)*-\w*r\w*f\w*\s+~"),  "rm -rf ~"),
    (re.compile(r"\bdd\s+if\s*="),                      "dd if="),
    (re.compile(r"\bmkfs\."),                           "mkfs."),
    (re.compile(r">\s*/dev/[sn]"),                      "> /dev/"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&"),        "fork bomb"),
    (re.compile(r"\bchmod\s+.*-[rR]\s+[07]{3}\s+/"),   "chmod nuke"),
]

# Extended patterns — applied to original (case-sensitive) command
_RE_IFS            = re.compile(r'\bIFS\s*=')
_RE_BRACE_SEMI     = re.compile(r'\{[^}]*;[^}]*\}')
_RE_ZMODLOAD       = re.compile(r'\bzmodload\b')
_RE_ZSH_EQ        = re.compile(r'(?<![=\w])=\w+')
_RE_JQ_ENV        = re.compile(r'\bjq\b.*\benv\b')
_RE_AT_SH         = re.compile(r'@sh')
_RE_HERE_STR      = re.compile(r'<<<\s*\$')
_RE_EVAL_VAR      = re.compile(r'\beval\s+["\']?\$')
_RE_PROC_SUBST    = re.compile(r'<\(|>\(')
_RE_NULL_BYTE     = re.compile(r'\\x00|\$\'\\000\'')
_RE_PRINTF_B      = re.compile(r'\bprintf\b.*%b')
_RE_ARRAY_IDX     = re.compile(r'\[\s*\$\(')
_RE_ENV_OVERRIDE  = re.compile(r'^[A-Z_]+=\S+\s+\S', re.MULTILINE)
_RE_GIT_RCE_KEYS  = re.compile(
    r'git\s+config.*\b(core\.fsmonitor|core\.hooksPath|core\.gitProxy|uploadpack\.packObjectsHook)\b',
    re.IGNORECASE,
)
_RE_GIT_CFG_ENV   = re.compile(r'\bgit\b.*--config-env[\s=]', re.IGNORECASE)
_RE_CD_GIT        = re.compile(r'\bcd\b.+(?:;|&&|\|\|).+\bgit\b|\bgit\b.+(?:;|&&|\|\|).+\bcd\b')
_RE_ANSI_C_QUOTE  = re.compile(r"\$'[^']*'|\$\"[^\"]*\"")
_RE_VAR_PIPE      = re.compile(r'\$\{?\w+\}?\s*\|')
_RE_VAR_REDIR     = re.compile(r'<\s*\$\{?\w+\}?')
_RE_PROC_ENVIRON  = re.compile(r'/proc/[0-9a-z_*]+/environ')
_RE_QUOTE_COMMENT = re.compile(r"""['"]\s*#\s*[^'"]*['"]""")
_RE_NORMALIZE_WS  = re.compile(r"\s+")
_UNICODE_SEPARATORS = '\u00a0\u200b\u2028\u2029\ufeff'

# ── sed -i safety (CC: sedValidation) ────────────────────────────────────────
# `sed -i` behaves differently on GNU (Linux) vs BSD (macOS):
#   Linux:   sed -i 's/foo/bar/' file      (no backup needed, OK on Linux)
#   macOS:   sed -i '' 's/foo/bar/' file   (requires empty-string suffix)
#   Both:    sed -i.bak 's/foo/bar/' file  (backup extension, always safe)
#
# We warn when sed -i is followed directly by a sed script (quote char after -i SPACE)
# without the BSD-portable '' or "" empty-string suffix.
#   -i 's/...'  → warn (Linux-only, silent file corruption on macOS)
#   -i ''       → OK  (BSD portable)
#   -i ""       → OK  (BSD portable)
#   -i.bak      → OK  (extension backup)
_RE_SED_I_WITH_SCRIPT  = re.compile(r'''-i\s+[\'"]''')          # -i followed by quote
_RE_SED_I_EMPTY_BACKUP = re.compile(r'''-i\s+[\'\"]{2}[\s]''')  # -i '' <space> (BSD safe)

# ── Destructive command soft-warning patterns ─────────────────────────────────
# These are warned (not blocked) — user may genuinely want them.
_DESTRUCTIVE_WARN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\brm\s+(-\w*\s+)*-\w*r\w*\s'), "recursive rm (data loss risk)"),
    (re.compile(r'\btruncate\s+'), "truncate (data loss risk)"),
    (re.compile(r'\bshred\s+'), "shred (permanent deletion)"),
    (re.compile(r'\bwipe\s+'), "wipe (permanent deletion)"),
    (re.compile(r'\bgit\s+reset\s+--hard\b'), "git reset --hard (discards uncommitted changes)"),
    (re.compile(r'\bgit\s+clean\s+(-\w*f\w*|-f)'), "git clean -f (permanent file deletion)"),
    (re.compile(r'\bgit\s+push\s+.*--force\b'), "git push --force (rewrites remote history)"),
    (re.compile(r'\bdrop\s+table\b', re.IGNORECASE), "DROP TABLE (permanent data loss)"),
    (re.compile(r'\bdelete\s+from\b', re.IGNORECASE), "DELETE FROM (may delete all rows)"),
]


def _check_sed_safety(command: str) -> str | None:
    """Warn if `sed -i` is used without a platform-portable backup argument.

    Returns a warning string, or None if safe.
    GNU sed and BSD/macOS sed have incompatible -i semantics:
    - GNU:  sed -i 's/foo/bar/' file         (Linux-only, fails on macOS)
    - BSD:  sed -i '' 's/foo/bar/' file      (portable, requires '' suffix)
    - Both: sed -i.bak 's/foo/bar/' file     (extension backup, always safe)
    """
    if "sed" not in command or "-i" not in command:
        return None
    # Has `-i <quote>` pattern (script immediately after -i, no backup)
    has_script_after_i = bool(_RE_SED_I_WITH_SCRIPT.search(command))
    # Has `-i '' ` or `-i "" ` (empty backup = BSD portable)
    has_empty_backup = bool(_RE_SED_I_EMPTY_BACKUP.search(command))
    # Has -i.ext (extension backup = always safe)
    has_ext_backup = bool(re.search(r'-i\.\w+', command))

    if has_script_after_i and not has_empty_backup and not has_ext_backup:
        return (
            "⚠️  Warning: `sed -i` without a backup suffix is not portable (fails on macOS/BSD). "
            "Consider `sed -i ''` for BSD compatibility, or use the file_edit tool for "
            "reliable cross-platform in-place editing."
        )
    return None


def _check_destructive(command: str) -> str | None:
    """Return a warning string if the command matches known destructive patterns.

    Does NOT block — the warning is prepended to the command output.
    """
    normalized = _RE_NORMALIZE_WS.sub(" ", command.strip()).lower()
    warnings = []
    for pattern, label in _DESTRUCTIVE_WARN_PATTERNS:
        if pattern.search(normalized):
            warnings.append(label)
    sed_warn = _check_sed_safety(command)
    if sed_warn:
        warnings.append("sed -i portability issue")

    if warnings:
        joined = ", ".join(warnings)
        return f"⚠️  Destructive operation warning: {joined}. Proceeding..."
    return None


def _validate_command(command: str) -> str | None:
    """Validate *command* against dangerous patterns.

    Returns an error string if the command should be blocked, or ``None`` if it
    is safe to proceed.  The original command string is checked without lower-
    casing so that case-sensitive patterns (e.g. ``IFS``, env-var overrides) are
    caught correctly.  A lower-cased copy is used only for patterns that are
    intentionally case-insensitive.
    """
    # ── Helpers ──────────────────────────────────────────────────────────────
    def _blocked(label: str) -> str:
        return (
            f"\u274c Command blocked: '{command}' matches a dangerous pattern "
            f"({label!r}). This command is never allowed."
        )

    # Normalize whitespace for case-insensitive legacy checks.
    normalized_lower = _RE_NORMALIZE_WS.sub(" ", command.strip()).lower()

    # ── Original 8 patterns (case-insensitive, whitespace-normalised) ────────
    for pattern, label in _DENIED_PATTERNS:
        if pattern.search(normalized_lower):
            return _blocked(label)

    # ── Extended CC-parity validators ────────────────────────────────────────
    # These checks operate on the *original* (non-lowercased) command so that
    # case-sensitive constructs (IFS, env-var names, Unicode escapes) are matched
    # correctly.

    # 1. IFS reassignment
    if _RE_IFS.search(command):
        return _blocked("IFS reassignment detected — may alter word splitting")

    # 2. Brace expansion with embedded semicolons
    if _RE_BRACE_SEMI.search(command):
        return _blocked("brace expansion with semicolon — possible command injection")

    # 3. Unicode whitespace / zero-width characters
    if any(c in command for c in _UNICODE_SEPARATORS):
        return _blocked("Unicode whitespace/zero-width character detected — possible invisible command separator")

    # 4. Zsh zmodload
    if _RE_ZMODLOAD.search(command):
        return _blocked("zmodload detected — may load Zsh modules that bypass restrictions")

    # 5. Zsh =cmd substitution
    if _RE_ZSH_EQ.search(command):
        return _blocked("Zsh =cmd substitution detected — may expand to unexpected full path")

    # 6. Control characters (excluding tab and newline)
    if any(ord(c) < 32 and c not in '\t\n' for c in command):
        return _blocked("control character in command — possible command injection")

    # 7. jq shell escape
    if _RE_JQ_ENV.search(command) or _RE_AT_SH.search(command):
        return _blocked("jq env/shell escape detected — may execute arbitrary shell code")

    # 8. Unbalanced backtick pairs
    if command.count('`') % 2 != 0:
        return _blocked("unbalanced backticks — possible hidden command substitution")

    # 9. Here-string with variable expansion
    if _RE_HERE_STR.search(command):
        return _blocked("here-string with variable expansion — possible env-var leakage")

    # 10. eval with variable expansion
    if _RE_EVAL_VAR.search(command):
        return _blocked("eval with variable expansion — arbitrary code execution risk")

    # 11. Process substitution
    if _RE_PROC_SUBST.search(command):
        return _blocked("process substitution detected — executes command in subshell")

    # 12. Null byte injection
    if _RE_NULL_BYTE.search(command):
        return _blocked("null byte injection detected — possible command smuggling")

    # 13. printf %b escape interpretation
    if _RE_PRINTF_B.search(command):
        return _blocked("printf %b detected — may interpret dangerous escape sequences")

    # 14. Subshell in array index
    if _RE_ARRAY_IDX.search(command):
        return _blocked("subshell in array index detected — executes code during subscript evaluation")

    # 15. Env-var override at invocation start
    if _RE_ENV_OVERRIDE.search(command):
        return _blocked("env-var override at invocation — may silently override PATH or other critical vars")

    # 16. Bare git repo defense: block RCE-capable git config keys
    if _RE_GIT_RCE_KEYS.search(command):
        return "Git config key blocked: core.fsmonitor/hooksPath/gitProxy can enable RCE"

    # 17. git --config-env flag
    if _RE_GIT_CFG_ENV.search(command):
        return _blocked("git --config-env flag — can inject RCE-capable config values via env vars")

    # 18. cd + git compound command
    if 'cd' in command and 'git' in command:
        if _RE_CD_GIT.search(command):
            return _blocked("compound cd+git command — may bypass bare repository security checks")

    # ── CC-sourced advanced validators ───────────────────────────────────────────

    # 19. ANSI-C quoting
    if _RE_ANSI_C_QUOTE.search(command):
        return _blocked("ANSI-C quoting ($'...' or $\"...\") detected — may obfuscate dangerous characters")

    # 20. Variable expansion in pipe or redirect
    if _RE_VAR_PIPE.search(command) or _RE_VAR_REDIR.search(command):
        return _blocked("variable expansion in pipe or redirect ($VAR | cmd) — may expand to unexpected path")

    # 21. /proc/*/environ access
    if _RE_PROC_ENVIRON.search(command):
        return _blocked("/proc/*/environ access blocked — environment files may contain secrets")

    # 22. Carriage return injection
    if '\r' in command or '\\r' in command:
        return _blocked("carriage return (\\r) detected — may cause shell/display desynchronization")

    # 23. Quote-comment desynchronization
    if _RE_QUOTE_COMMENT.search(command):
        return _blocked("quote-comment desynchronization detected — # inside quotes may cause parser confusion")

    # 24. Brace expansion depth tracking (CC: validateBraceExpansion).
    #     Attack vector: git diff {@'{'0},--output=/tmp/pwned}
    #     A quoted '{' hides an extra OPEN brace; after stripping quotes the shell
    #     sees more CLOSE braces than OPEN braces, enabling path injection.
    def _count_unquoted_braces(cmd: str) -> tuple[int, int]:
        """Count unquoted { and } in a command string (respects single/double quotes)."""
        opens = closes = 0
        in_single = in_double = False
        i = 0
        while i < len(cmd):
            c = cmd[i]
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif c == '\\' and (in_single or in_double):
                i += 1  # skip escaped char
            elif not in_single and not in_double:
                if c == '{':
                    opens += 1
                elif c == '}':
                    closes += 1
            i += 1
        return opens, closes

    opens, closes = _count_unquoted_braces(command)
    if closes > opens:
        return _blocked(
            f"brace expansion imbalance ({opens} opens, {closes} closes) — "
            "may exploit quote-stripping to inject file paths"
        )

    return None  # command passed all checks


@tool(args_schema=TerminalExecArgs)
def terminal_exec(command: str, cwd: str = "", timeout: int = 0) -> str:
    """Execute a shell command and return its output.

    Args:
        command: The command to execute.
        cwd: Working directory. Defaults to workspace root.
        timeout: Max execution time in seconds. 0 = use default (30s).

    Returns:
        Command stdout/stderr output, truncated if too long.
    """
    # Block catastrophically destructive commands regardless of cwd.
    err = _validate_command(command)
    if err:
        return err

    # Warn (but allow) destructive commands — prepend warning to output.
    _destruction_warning = _check_destructive(command)

    # Sandbox cwd to workspace boundary
    if cwd:
        safe_cwd = resolve_path_safe(cwd)
        if safe_cwd is None:
            return f"❌ Error: cwd '{cwd}' is outside the workspace. Access denied."
        work_dir = safe_cwd
    else:
        work_dir = config.WORKSPACE_DIR
    max_timeout = timeout if timeout > 0 else config.TOOL_TIMEOUT

    # ── Docker sandbox (when enabled and available) ────────────
    if config.SANDBOX_ENABLED:
        from agent.sandbox import SANDBOX_AVAILABLE, sandbox_exec
        if SANDBOX_AVAILABLE:
            raw = sandbox_exec(command, work_dir, max_timeout)
            return truncate_output(raw)
        # else: fall through to direct execution with a warning note

    # ── Direct execution ───────────────────────────────────────
    proc = None
    try:
        # shell=True: command comes from the LLM/user request, not untrusted input.
        # Sandboxed via work_dir (workspace-bound cwd) and configurable timeout.
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=_safe_env(),
        )
        try:
            stdout, stderr = proc.communicate(timeout=max_timeout)
        except subprocess.TimeoutExpired:
            # Kill the process tree — without this the child keeps running after
            # communicate() raises, which leaks resources and can cause hangs.
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            except Exception as e:
                logger.warning("Unexpected error during process communication: %s", e)
                stdout, stderr = "", ""
            return f"Command timed out after {max_timeout}s: {command}"

        returncode = proc.returncode
        status = (
            f"Exit code: {returncode} OK"
            if returncode == 0
            else f"Exit code: {returncode} ERROR"
        )

        # Keep stdout and stderr clearly separated.
        # Stderr is appended LAST so it's least likely to be truncated — it
        # usually contains the most useful diagnostic information on failure.
        sections = [status]
        if stdout:
            sections.append(stdout.rstrip())
        if stderr:
            sections.append(f"[STDERR]\n{stderr.rstrip()}")
        if not stdout and not stderr:
            sections.append("(no output)")

        raw = "\n\n".join(sections)
        if _destruction_warning:
            raw = _destruction_warning + "\n\n" + raw

        # Universal truncation — saves full output to disk if truncated
        return truncate_output(raw)

    except FileNotFoundError:
        return f"Error: Working directory '{work_dir}' not found."
    except Exception as e:
        return f"Error executing command: {e}"
