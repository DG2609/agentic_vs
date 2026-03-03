"""
Tool: terminal_exec — run shell commands with timeout and output capture.
All outputs go through universal truncation.
"""
import asyncio
import subprocess
import time
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_path_safe
from models.tool_schemas import TerminalExecArgs


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
    # Block catastrophically destructive commands regardless of cwd
    _DENIED_PATTERNS = [
        "rm -rf /", "rm -rf /*", "rm -rf ~",  # Nuke root/home
        "dd if=", "mkfs.",                      # Disk wipe / format
        "> /dev/sda", "> /dev/nvme",            # Direct disk write
        ":(){ :|:& };:",                        # Fork bomb
        "chmod -R 777 /", "chmod -R 000 /",    # Permission nuke
    ]
    cmd_lower = command.strip().lower()
    for pattern in _DENIED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return (
                f"❌ Command blocked: '{command}' matches a dangerous pattern "
                f"({pattern!r}). This command is never allowed."
            )

    # Sandbox cwd to workspace boundary
    if cwd:
        safe_cwd = resolve_path_safe(cwd)
        if safe_cwd is None:
            return f"❌ Error: cwd '{cwd}' is outside the workspace. Access denied."
        work_dir = safe_cwd
    else:
        work_dir = config.WORKSPACE_DIR
    max_timeout = timeout if timeout > 0 else config.TOOL_TIMEOUT

    try:
        # shell=True: command comes from the LLM/user request, not untrusted input.
        # Sandboxed via work_dir (workspace-bound cwd) and configurable timeout.
        result = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=max_timeout,
            encoding="utf-8",
            errors="replace",
        )

        status = (
            f"Exit code: {result.returncode} ✅"
            if result.returncode == 0
            else f"Exit code: {result.returncode} ❌"
        )

        # Keep stdout and stderr clearly separated.
        # Stderr is appended LAST so it's least likely to be truncated — it
        # usually contains the most useful diagnostic information on failure.
        sections = [status]
        if result.stdout:
            sections.append(result.stdout.rstrip())
        if result.stderr:
            sections.append(f"[STDERR]\n{result.stderr.rstrip()}")
        if not result.stdout and not result.stderr:
            sections.append("(no output)")

        raw = "\n\n".join(sections)

        # Universal truncation — saves full output to disk if truncated
        return truncate_output(raw)

    except subprocess.TimeoutExpired:
        return f"⏱️ Command timed out after {max_timeout}s: {command}"
    except FileNotFoundError:
        return f"Error: Working directory '{work_dir}' not found."
    except Exception as e:
        return f"Error executing command: {e}"
