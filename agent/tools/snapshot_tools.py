"""
Snapshot tools — let the agent list and revert file snapshots.

Auto-snapshots are created by the pre-hook in graph.py before file writes.
These tools let the agent (or user) inspect and revert to previous states.
"""

from langchain_core.tools import tool
from agent.snapshots import list_snapshots, revert_snapshot, get_snapshot


@tool
def snapshot_list(limit: int = 20) -> str:
    """List recent file snapshots (auto-created before file edits).

    Each snapshot contains the original file contents before a write/edit.
    Returns newest first with ID, timestamp, message, and files.

    Args:
        limit: Max snapshots to return (default 20).
    """
    snaps = list_snapshots(limit=limit)
    if not snaps:
        return "No snapshots found."

    lines = [f"Found {len(snaps)} snapshot(s):\n"]
    for s in snaps:
        import time
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["timestamp"]))
        files = [f["path"] for f in s.get("files", [])]
        file_str = ", ".join(files[:5])
        if len(files) > 5:
            file_str += f" (+{len(files) - 5} more)"
        lines.append(f"  {s['id']}  [{ts}]  {s.get('message', '')}  files: {file_str}")
    return "\n".join(lines)


@tool
def snapshot_revert(snapshot_id: str) -> str:
    """Revert files to a previous snapshot state.

    Restores the original file contents from before a write/edit operation.
    Files that didn't exist before the edit will be removed.

    Args:
        snapshot_id: The snapshot ID to revert to (from snapshot_list).
    """
    try:
        restored = revert_snapshot(snapshot_id)
        if not restored:
            return f"Snapshot {snapshot_id} reverted but no files needed restoring."
        return f"Reverted {len(restored)} file(s):\n" + "\n".join(f"  - {f}" for f in restored)
    except ValueError as e:
        return f"Error: {e}"


@tool
def snapshot_info(snapshot_id: str) -> str:
    """Get detailed info about a specific snapshot.

    Args:
        snapshot_id: The snapshot ID to inspect.
    """
    meta = get_snapshot(snapshot_id)
    if not meta:
        return f"Snapshot not found: {snapshot_id}"

    import time
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(meta["timestamp"]))
    lines = [
        f"Snapshot: {meta['id']}",
        f"Created:  {ts}",
        f"Message:  {meta.get('message', 'N/A')}",
        f"Workspace: {meta.get('workspace', 'N/A')}",
        f"Files ({len(meta.get('files', []))}):",
    ]
    for f in meta.get("files", []):
        existed = "existed" if f.get("existed") else "new file"
        size = f.get("size", "?")
        lines.append(f"  - {f['path']}  ({existed}, {size} bytes)")
    return "\n".join(lines)
