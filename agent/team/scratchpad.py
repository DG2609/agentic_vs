"""
Shared scratchpad for cross-worker knowledge.

All workers in a team session can read/write here.
Root defaults to config.TEAM_SCRATCHPAD_DIR relative to workspace.
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Scratchpad:
    """File-based shared memory for a team session."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, filename: str) -> str:
        # Prevent directory traversal
        safe = os.path.basename(filename)
        return os.path.join(self.root, safe)

    def write(self, filename: str, content: str) -> None:
        """Write content to a named scratchpad file (atomic via tmp→replace)."""
        os.makedirs(self.root, exist_ok=True)
        path = Path(self._path(filename))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        logger.debug(f"[scratchpad] wrote {filename} ({len(content)} chars)")

    def read(self, filename: str) -> str:
        """Read a scratchpad file. Returns empty string if not found."""
        path = self._path(filename)
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def list_files(self) -> list[str]:
        """List all files in the scratchpad."""
        if not os.path.exists(self.root):
            return []
        return sorted(
            f for f in os.listdir(self.root)
            if os.path.isfile(os.path.join(self.root, f))
        )
