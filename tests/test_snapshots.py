"""Tests for agent/snapshots.py — file snapshot/revert system."""
import json
import os
import shutil
import tempfile
import time
from unittest.mock import patch

import pytest

# Patch config before importing snapshots
import config
_orig_data_dir = config.DATA_DIR


@pytest.fixture(autouse=True)
def tmp_snapshot_dir(tmp_path):
    """Use temp dir for snapshots during tests."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    with patch("agent.snapshots.SNAPSHOT_DIR", snap_dir):
        yield snap_dir


@pytest.fixture
def workspace(tmp_path):
    """Create a temp workspace with sample files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hello')\n")
    (ws / "sub").mkdir()
    (ws / "sub" / "data.txt").write_text("line1\nline2\n")
    return str(ws)


class TestCreateSnapshot:
    def test_basic_snapshot(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, SNAPSHOT_DIR
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot(
                [os.path.join(workspace, "hello.py")],
                message="file_edit: hello.py",
                workspace=workspace,
            )
        assert snap_id
        meta_path = tmp_snapshot_dir / snap_id / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["id"] == snap_id
        assert meta["message"] == "file_edit: hello.py"
        assert len(meta["files"]) == 1
        assert meta["files"][0]["existed"] is True

    def test_snapshot_preserves_content(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot
        fpath = os.path.join(workspace, "hello.py")
        original = open(fpath).read()
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot([fpath], workspace=workspace)
        backup = (tmp_snapshot_dir / snap_id / "files" / "hello.py").read_text()
        assert backup == original

    def test_snapshot_nonexistent_file(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot(
                [os.path.join(workspace, "missing.py")],
                workspace=workspace,
            )
        meta = json.loads((tmp_snapshot_dir / snap_id / "metadata.json").read_text())
        assert meta["files"][0]["existed"] is False

    def test_snapshot_multiple_files(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot
        paths = [
            os.path.join(workspace, "hello.py"),
            os.path.join(workspace, "sub", "data.txt"),
        ]
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot(paths, workspace=workspace)
        meta = json.loads((tmp_snapshot_dir / snap_id / "metadata.json").read_text())
        assert len(meta["files"]) == 2
        assert all(f["existed"] for f in meta["files"])


class TestListSnapshots:
    def test_empty_list(self, tmp_snapshot_dir):
        from agent.snapshots import list_snapshots
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            result = list_snapshots()
        assert result == []

    def test_list_returns_newest_first(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, list_snapshots
        fpath = os.path.join(workspace, "hello.py")
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            id1 = create_snapshot([fpath], message="first", workspace=workspace)
            id2 = create_snapshot([fpath], message="second", workspace=workspace)
            snaps = list_snapshots()
        assert len(snaps) == 2
        assert snaps[0]["id"] == id2
        assert snaps[1]["id"] == id1


class TestRevertSnapshot:
    def test_revert_restores_content(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, revert_snapshot
        fpath = os.path.join(workspace, "hello.py")
        original = open(fpath).read()
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot([fpath], workspace=workspace)
        # Modify the file
        with open(fpath, "w") as f:
            f.write("MODIFIED CONTENT")
        assert open(fpath).read() == "MODIFIED CONTENT"
        # Revert
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            restored = revert_snapshot(snap_id)
        assert len(restored) == 1
        assert open(fpath).read() == original

    def test_revert_removes_new_file(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, revert_snapshot
        new_file = os.path.join(workspace, "new.py")
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot([new_file], workspace=workspace)
        # Create the file (simulating a file_write)
        with open(new_file, "w") as f:
            f.write("new content")
        assert os.path.exists(new_file)
        # Revert — should remove it
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            restored = revert_snapshot(snap_id)
        assert not os.path.exists(new_file)

    def test_revert_nonexistent_raises(self, tmp_snapshot_dir):
        from agent.snapshots import revert_snapshot
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            with pytest.raises(ValueError, match="not found"):
                revert_snapshot("nonexistent_id")


class TestDeleteSnapshot:
    def test_delete(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, delete_snapshot, list_snapshots
        fpath = os.path.join(workspace, "hello.py")
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot([fpath], workspace=workspace)
            assert delete_snapshot(snap_id) is True
            assert list_snapshots() == []

    def test_delete_nonexistent(self, tmp_snapshot_dir):
        from agent.snapshots import delete_snapshot
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            assert delete_snapshot("nope") is False


class TestPruning:
    def test_old_snapshots_pruned(self, workspace, tmp_snapshot_dir):
        from agent.snapshots import create_snapshot, list_snapshots, _prune_old_snapshots
        fpath = os.path.join(workspace, "hello.py")
        with patch("agent.snapshots.SNAPSHOT_DIR", tmp_snapshot_dir):
            snap_id = create_snapshot([fpath], workspace=workspace)
            # Backdate the metadata
            meta_path = tmp_snapshot_dir / snap_id / "metadata.json"
            meta = json.loads(meta_path.read_text())
            meta["timestamp"] = time.time() - (8 * 24 * 3600)  # 8 days ago
            meta_path.write_text(json.dumps(meta))
            removed = _prune_old_snapshots()
        assert removed == 1
