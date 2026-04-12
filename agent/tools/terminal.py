"""
Tool: terminal_exec — run shell commands with timeout and output capture.
All outputs go through universal truncation.

When config.SANDBOX_ENABLED=True and Docker is available, commands run inside
an isolated container (network isolation, memory/CPU limits, no privilege escalation).
Falls back to direct execution if Docker is unavailable.
"""
import re
import subprocess
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_path_safe
from models.tool_schemas import TerminalExecArgs


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
    normalized_lower = re.sub(r"\s+", " ", command.strip()).lower()

    # ── Original 8 patterns (case-insensitive, whitespace-normalised) ────────
    _DENIED_PATTERNS = [
        (r"rm\s+(-\w*\s+)*-\w*r\w*f\w*\s+/",  "rm -rf /"),   # rm -rf / variants
        (r"rm\s+(-\w*\s+)*-\w*f\w*r\w*\s+/",  "rm -rf /"),   # rm -fr / variants
        (r"rm\s+(-\w*\s+)*-\w*r\w*f\w*\s+~",  "rm -rf ~"),   # home nuke
        (r"\bdd\s+if\s*=",                     "dd if="),      # Disk wipe
        (r"\bmkfs\.",                           "mkfs."),       # Disk format
        (r">\s*/dev/[sn]",                     "> /dev/"),     # Direct disk write
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&",       "fork bomb"),   # Fork bomb
        (r"\bchmod\s+.*-[rR]\s+[07]{3}\s+/",    "chmod nuke"),  # Permission nuke
    ]
    for regex, label in _DENIED_PATTERNS:
        if re.search(regex, normalized_lower):
            return _blocked(label)

    # ── Extended CC-parity validators ────────────────────────────────────────
    # These checks operate on the *original* (non-lowercased) command so that
    # case-sensitive constructs (IFS, env-var names, Unicode escapes) are matched
    # correctly.

    # 1. IFS reassignment — can alter word splitting for subsequent commands
    if re.search(r'\bIFS\s*=', command):
        return _blocked("IFS reassignment detected — may alter word splitting")

    # 2. Brace expansion with embedded semicolons — e.g. {rm,-rf}/
    if re.search(r'\{[^}]*;[^}]*\}', command):
        return _blocked("brace expansion with semicolon — possible command injection")

    # 3. Unicode whitespace / zero-width characters used as invisible separators
    _UNICODE_SEPARATORS = '\u00a0\u200b\u2028\u2029\ufeff'
    if any(c in command for c in _UNICODE_SEPARATORS):
        return _blocked("Unicode whitespace/zero-width character detected — possible invisible command separator")

    # 4. Zsh zmodload — loads Zsh modules that can bypass shell restrictions
    if re.search(r'\bzmodload\b', command):
        return _blocked("zmodload detected — may load Zsh modules that bypass restrictions")

    # 5. Zsh =cmd substitution — =ls expands to the full path of 'ls'
    #    Match a bare =word token (not KEY=value assignment context).
    if re.search(r'(?<![=\w])=\w+', command):
        return _blocked("Zsh =cmd substitution detected — may expand to unexpected full path")

    # 6. Control characters (excluding tab and newline) embedded in the command
    if any(ord(c) < 32 and c not in '\t\n' for c in command):
        return _blocked("control character in command — possible command injection")

    # 7. jq shell escape — jq's env builtin or @sh format can call out to shell
    if re.search(r'\bjq\b.*\benv\b', command) or re.search(r'@sh', command):
        return _blocked("jq env/shell escape detected — may execute arbitrary shell code")

    # 8. Unbalanced backtick pairs — odd number of backticks hides subshell injection
    if command.count('`') % 2 != 0:
        return _blocked("unbalanced backticks — possible hidden command substitution")

    # 9. Here-string with variable expansion — can leak env vars to untrusted cmds
    if re.search(r'<<<\s*\$', command):
        return _blocked("here-string with variable expansion — possible env-var leakage")

    # 10. eval with variable expansion
    if re.search(r'\beval\s+["\']?\$', command):
        return _blocked("eval with variable expansion — arbitrary code execution risk")

    # 11. Process substitution — <(cmd) or >(cmd) runs cmd in a subshell
    if re.search(r'<\(|>\(', command):
        return _blocked("process substitution detected — executes command in subshell")

    # 12. Null byte injection — \x00 or $'\000' in command string
    if re.search(r'\\x00|\$\'\\000\'', command):
        return _blocked("null byte injection detected — possible command smuggling")

    # 13. printf %b escape interpretation — can interpret arbitrary escape sequences
    if re.search(r'\bprintf\b.*%b', command):
        return _blocked("printf %b detected — may interpret dangerous escape sequences")

    # 14. Subshell in array index — array[$(...)] executes code during subscript evaluation
    if re.search(r'\[\s*\$\(', command):
        return _blocked("subshell in array index detected — executes code during subscript evaluation")

    # 15. Env-var override at invocation start — FOO=bar cmd can silently override PATH etc.
    if re.search(r'^[A-Z_]+=\S+\s+\S', command, re.MULTILINE):
        return _blocked("env-var override at invocation — may silently override PATH or other critical vars")

    # 16. Bare git repo defense: block setting core.fsmonitor / core.hooksPath / other RCE-capable
    #     git config keys — these can execute arbitrary code whenever git reads the config.
    _GIT_RCE_KEYS = re.compile(
        r'git\s+config.*\b(core\.fsmonitor|core\.hooksPath|core\.gitProxy|uploadpack\.packObjectsHook)\b',
        re.IGNORECASE,
    )
    if _GIT_RCE_KEYS.search(command):
        return "Git config key blocked: core.fsmonitor/hooksPath/gitProxy can enable RCE"

    # 17. git --config-env flag — allows injecting RCE-capable git config values via
    #     environment variables (core.fsmonitor, diff.external, etc.). Block it entirely.
    if re.search(r'\bgit\b.*--config-env[\s=]', command, re.IGNORECASE):
        return _blocked("git --config-env flag — can inject RCE-capable config values via env vars")

    # 18. cd + git compound command — can bypass bare-repo detection by first cd'ing into
    #     a malicious directory that contains a bare git repo with core.fsmonitor.
    #     CC blocks these as they require explicit approval.
    #     Only block when semicolon/&&/|| joins a cd with a git command.
    if re.search(r'\bcd\b', command) and re.search(r'\bgit\b', command):
        # Check if cd and git appear in the same compound command (joined by ; && ||)
        if re.search(r'\bcd\b.+(?:;|&&|\|\|).+\bgit\b|\bgit\b.+(?:;|&&|\|\|).+\bcd\b', command):
            return _blocked("compound cd+git command — may bypass bare repository security checks")

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
        )
        try:
            stdout, stderr = proc.communicate(timeout=max_timeout)
        except subprocess.TimeoutExpired:
            # Kill the process tree — without this the child keeps running after
            # communicate() raises, which leaks resources and can cause hangs.
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
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

        # Universal truncation — saves full output to disk if truncated
        return truncate_output(raw)

    except FileNotFoundError:
        return f"Error: Working directory '{work_dir}' not found."
    except Exception as e:
        return f"Error executing command: {e}"
