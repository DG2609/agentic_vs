"""
Docker-based execution sandbox for terminal_exec.

When SANDBOX_ENABLED=True and Docker is available, terminal commands run inside
an isolated Docker container with:
  - Workspace mounted read-write (or read-only with SANDBOX_READONLY=True)
  - Network isolation (--network none by default)
  - Memory + CPU + PID limits
  - No privilege escalation (--security-opt no-new-privileges)
  - Auto-removal on exit (--rm)
  - Named container for reliable cleanup on timeout

Falls back transparently to direct subprocess execution if Docker is unavailable.

Config options (all in config.py / .env):
  SANDBOX_ENABLED     bool   False
  SANDBOX_IMAGE       str    "python:3.12-slim"
  SANDBOX_NETWORK     str    "none"   (full network isolation)
  SANDBOX_MEMORY      str    "512m"
  SANDBOX_CPUS        str    "1.0"
  SANDBOX_PIDS_LIMIT  int    100
  SANDBOX_READONLY    bool   False    (workspace mounted read-write by default)
"""

import logging
import shutil
import subprocess
import uuid

import config

logger = logging.getLogger(__name__)


# ── Availability check (cached once at import time) ────────────

def _check_docker() -> bool:
    """Return True if docker CLI is found and daemon is responsive."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


SANDBOX_AVAILABLE: bool = _check_docker()

if SANDBOX_AVAILABLE:
    logger.info("[sandbox] Docker detected — container sandbox ready")
else:
    logger.debug("[sandbox] Docker not detected — sandbox will fall back to direct execution")


# ── Core sandbox executor ──────────────────────────────────────

def sandbox_exec(command: str, work_dir: str, timeout: int) -> str:
    """Execute a shell command inside an isolated Docker container.

    The workspace is mounted at the same absolute path inside the container,
    so relative references in commands resolve correctly.

    Args:
        command:  Shell command string (passed to `sh -c`).
        work_dir: Absolute path to mount as the working directory.
        timeout:  Max execution time in seconds (subprocess timeout adds
                  10 extra seconds to account for container startup).

    Returns:
        Formatted output string (same format as terminal_exec):
        "Exit code: N ✅/❌ [sandbox]\\n\\n<stdout>\\n\\n[STDERR]\\n<stderr>"
    """
    # Generate a unique container name for reliable cleanup on timeout
    container_name = f"shadowdev-{uuid.uuid4().hex[:12]}"

    # Workspace mount: read-write or read-only
    mount_mode = "ro" if config.SANDBOX_READONLY else "rw"
    volume_spec = f"{work_dir}:{work_dir}:{mount_mode}"

    docker_cmd = [
        "docker", "run",
        "--rm",
        "--name", container_name,
        "--network", config.SANDBOX_NETWORK,
        "--memory", config.SANDBOX_MEMORY,
        "--cpus", config.SANDBOX_CPUS,
        "--pids-limit", str(config.SANDBOX_PIDS_LIMIT),
        "--security-opt", "no-new-privileges",
        "-v", volume_spec,
        "-w", work_dir,
        config.SANDBOX_IMAGE,
        "sh", "-c", command,
    ]

    # Allow extra time for Docker container startup overhead
    subprocess_timeout = timeout + 10

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
            encoding="utf-8",
            errors="replace",
        )

        status = (
            f"Exit code: {result.returncode} ✅ [sandbox]"
            if result.returncode == 0
            else f"Exit code: {result.returncode} ❌ [sandbox]"
        )

        sections = [status]
        if result.stdout:
            sections.append(result.stdout.rstrip())
        if result.stderr:
            sections.append(f"[STDERR]\n{result.stderr.rstrip()}")
        if not result.stdout and not result.stderr:
            sections.append("(no output)")

        return "\n\n".join(sections)

    except subprocess.TimeoutExpired:
        _kill_container(container_name)
        return f"⏱️ Sandboxed command timed out after {timeout}s: {command}"

    except FileNotFoundError:
        return "Error: docker executable not found. Cannot run in sandbox."

    except Exception as e:
        _kill_container(container_name)
        return f"Error in sandbox execution: {type(e).__name__}: {e}"


def _kill_container(name: str) -> None:
    """Best-effort stop and remove a named container."""
    try:
        subprocess.run(
            ["docker", "stop", "--time", "1", name],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


# ── Pull image if not present ──────────────────────────────────

def pull_sandbox_image() -> bool:
    """Pull the configured sandbox image if not already local.

    Returns True on success, False on failure.
    Safe to call multiple times (no-op if image exists).
    """
    if not SANDBOX_AVAILABLE:
        return False
    try:
        result = subprocess.run(
            ["docker", "pull", config.SANDBOX_IMAGE],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("[sandbox] Pulled image: %s", config.SANDBOX_IMAGE)
            return True
        logger.warning("[sandbox] Failed to pull image '%s': %s",
                       config.SANDBOX_IMAGE, result.stderr.strip()[:200])
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[sandbox] Image pull error: %s", e)
        return False
