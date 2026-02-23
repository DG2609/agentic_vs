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
    work_dir = cwd or config.WORKSPACE_DIR
    max_timeout = timeout if timeout > 0 else config.TOOL_TIMEOUT

    try:
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

        output_parts = []

        if result.stdout:
            output_parts.append(result.stdout)

        if result.stderr:
            output_parts.append(f"[STDERR]\n{result.stderr}")

        if not output_parts:
            output_parts.append("(no output)")

        status = f"✅ Exit code: {result.returncode}" if result.returncode == 0 else f"❌ Exit code: {result.returncode}"
        raw = f"{status}\n\n" + "\n".join(output_parts)

        # Universal truncation — saves full output to disk if truncated
        return truncate_output(raw)

    except subprocess.TimeoutExpired:
        return f"⏱️ Command timed out after {max_timeout}s: {command}"
    except FileNotFoundError:
        return f"Error: Working directory '{work_dir}' not found."
    except Exception as e:
        return f"Error executing command: {e}"
