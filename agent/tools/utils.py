"""
Shared utilities for agent tools.

Centralizes:
- Path resolution with sandboxing (security)
- Ignore dir/file constants (DRY)
"""
import logging
import os
import config

logger = logging.getLogger(__name__)


# ── Shared ignore lists ──────────────────────────────────────

IGNORE_DIRS = frozenset({
    "__pycache__", ".git", ".svn", "node_modules", ".venv", "venv",
    "env", "dist", "build", ".next", ".cache", ".tox",
    "target", "bin", "obj", ".idea", ".vscode",
})

BINARY_EXT = frozenset({
    ".exe", ".dll", ".so", ".o", ".obj", ".bin", ".dat", ".db",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
    ".pyc", ".class", ".whl",
    ".mp3", ".mp4", ".avi", ".wav",
    ".slx", ".mdl", ".mat", ".fig", ".mexa64", ".mexw64",
})


# ── Path resolution with sandboxing ─────────────────────────

def resolve_path(p: str, workspace: str = None) -> str:
    """Resolve a tool path safely within the workspace boundary.

    - Relative paths are joined with workspace.
    - Absolute paths are allowed ONLY if they fall within the workspace.
    - Symlinks are resolved to prevent escaping via symlinks.

    Args:
        p: The path from the tool argument (relative or absolute).
        workspace: Workspace root. Defaults to config.WORKSPACE_DIR.

    Returns:
        Resolved absolute path.

    Raises:
        ValueError: If the resolved path is outside the workspace.
    """
    ws = os.path.realpath(workspace or config.WORKSPACE_DIR)

    if os.path.isabs(p):
        resolved = os.path.realpath(p)
    else:
        resolved = os.path.realpath(os.path.join(ws, p))

    # Security: ensure path is within workspace
    if not resolved.startswith(ws + os.sep) and resolved != ws:
        raise ValueError(
            f"Access denied: path '{p}' resolves to '{resolved}' "
            f"which is outside workspace '{ws}'"
        )

    return resolved


def resolve_path_safe(p: str, workspace: str = None) -> str | None:
    """Like resolve_path but returns None instead of raising on violation.

    Logs a debug message when access is denied. Use this in tools that want
    to return a friendly error message.
    """
    try:
        return resolve_path(p, workspace)
    except ValueError as e:
        logger.debug(f"[security] resolve_path_safe denied: {e}")
        return None


def resolve_tool_path(p: str, workspace: str = None) -> str:
    """Standard path resolver for tools — always returns a usable path.

    Tries to resolve safely within workspace. If the path is outside the
    workspace boundary, logs a security warning and clamps to workspace root
    rather than passing the external path through.

    Unlike resolve_path (raises ValueError) and resolve_path_safe (returns None),
    this never fails — making it safe to use directly in tool implementations.
    """
    resolved = resolve_path_safe(p, workspace)
    if resolved is not None:
        return resolved
    # Path is outside workspace — warn and clamp instead of passthrough
    ws = os.path.realpath(workspace or config.WORKSPACE_DIR)
    logger.warning(
        f"[security] resolve_tool_path: '{p}' is outside workspace '{ws}' "
        f"— clamping to workspace root"
    )
    if os.path.isabs(p):
        return ws  # Never expose external absolute paths
    return os.path.join(ws, p)
