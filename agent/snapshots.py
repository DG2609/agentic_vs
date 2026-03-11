"""
Snapshot/Revert system — git-based undo before every file modification.

Before file_write/file_edit/file_edit_batch, the original file is copied
to data/snapshots/{id}/. Users can list and revert to any snapshot.

Each snapshot stores:
- metadata.json: id, timestamp, message, files list
- files/: original copies of modified files (relative to workspace)

Auto-pruning: snapshots older than 7 days are removed on create.
"""

import json
import logging
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

import config

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(config.DATA_DIR) / "snapshots"
_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days


def _prune_old_snapshots() -> int:
    """Remove snapshots older than 7 days. Returns count removed."""
    if not SNAPSHOT_DIR.exists():
        return 0
    cutoff = time.time() - _MAX_AGE_SECONDS
    removed = 0
    for entry in SNAPSHOT_DIR.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if meta_path.exists():
            try:
                ts = json.loads(meta_path.read_text()).get("timestamp", 0)
                if ts < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
                    continue
            except (json.JSONDecodeError, OSError):
                pass
        # If no metadata, check dir mtime
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("[snapshots] Pruned %d old snapshots", removed)
    return removed


def create_snapshot(
    file_paths: list[str],
    message: str = "",
    workspace: str = "",
) -> str:
    """Backup files before modification.

    Args:
        file_paths: Absolute paths to files that will be modified.
        message: Human-readable description (e.g. tool name + args).
        workspace: Workspace root for relative path computation.

    Returns:
        Snapshot ID string.
    """
    workspace = workspace or str(config.WORKSPACE_DIR)
    snap_id = f"{int(time.time())}_{uuid4().hex[:8]}"
    snap_dir = SNAPSHOT_DIR / snap_id
    files_dir = snap_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    file_records = []
    for fpath in file_paths:
        fpath = str(fpath)
        if not os.path.exists(fpath):
            file_records.append({"path": fpath, "existed": False})
            continue

        # Compute relative path for storage
        try:
            rel = os.path.relpath(fpath, workspace)
        except ValueError:
            rel = os.path.basename(fpath)

        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(fpath, dest)
            file_records.append({"path": rel, "existed": True, "size": os.path.getsize(fpath)})
        except OSError as e:
            logger.warning("[snapshots] Failed to copy %s: %s", fpath, e)
            file_records.append({"path": rel, "existed": True, "error": str(e)})

    meta = {
        "id": snap_id,
        "timestamp": time.time(),
        "message": message,
        "workspace": workspace,
        "files": file_records,
    }
    (snap_dir / "metadata.json").write_text(json.dumps(meta, indent=2))
    logger.info("[snapshots] Created snapshot %s (%d files)", snap_id, len(file_records))

    # Background prune
    try:
        _prune_old_snapshots()
    except Exception:
        pass

    return snap_id


def list_snapshots(limit: int = 50) -> list[dict]:
    """List recent snapshots, newest first."""
    if not SNAPSHOT_DIR.exists():
        return []
    snapshots = []
    for entry in SNAPSHOT_DIR.iterdir():
        if not entry.is_dir():
            continue
        meta_path = entry / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
            snapshots.append(meta)
        except (json.JSONDecodeError, OSError):
            continue
    snapshots.sort(key=lambda s: s.get("timestamp", 0), reverse=True)
    return snapshots[:limit]


def get_snapshot(snap_id: str) -> dict | None:
    """Get metadata for a specific snapshot."""
    meta_path = SNAPSHOT_DIR / snap_id / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def revert_snapshot(snap_id: str) -> list[str]:
    """Restore files from a snapshot.

    Returns list of restored file paths.
    """
    snap_dir = SNAPSHOT_DIR / snap_id
    meta_path = snap_dir / "metadata.json"
    if not meta_path.exists():
        raise ValueError(f"Snapshot not found: {snap_id}")

    meta = json.loads(meta_path.read_text())
    workspace = meta.get("workspace", str(config.WORKSPACE_DIR))
    files_dir = snap_dir / "files"
    restored = []

    for record in meta.get("files", []):
        rel_path = record["path"]
        existed = record.get("existed", False)
        target = os.path.join(workspace, rel_path)

        if not existed:
            # File didn't exist before — remove it if it was created
            if os.path.exists(target):
                os.remove(target)
                restored.append(f"REMOVED {rel_path}")
            continue

        source = files_dir / rel_path
        if not source.exists():
            logger.warning("[snapshots] Backup file missing: %s", source)
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(str(source), target)
        restored.append(rel_path)

    logger.info("[snapshots] Reverted snapshot %s (%d files)", snap_id, len(restored))
    return restored


def delete_snapshot(snap_id: str) -> bool:
    """Delete a snapshot."""
    snap_dir = SNAPSHOT_DIR / snap_id
    if not snap_dir.exists():
        return False
    shutil.rmtree(snap_dir, ignore_errors=True)
    return True
