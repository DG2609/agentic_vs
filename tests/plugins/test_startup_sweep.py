"""Tests for PluginManager.startup_sweep — reconciles disk dirs with DB rows."""
from pathlib import Path

from agent.plugins.manager import PluginManager


def _make_mgr(tmp_path: Path) -> PluginManager:
    return PluginManager(
        hub_index_url="http://example.invalid/index.json",
        install_root=tmp_path / "plugins",
        temp_root=tmp_path / "tmp",
        db_path=tmp_path / "plugins.db",
        cache_dir=tmp_path / "cache",
    )


def test_startup_sweep_removes_orphan_install_dirs(tmp_path: Path):
    mgr = _make_mgr(tmp_path)
    orphan = tmp_path / "plugins" / "ghost-1.0.0"
    orphan.mkdir(parents=True)
    (orphan / "plugin.json").write_text("{}", encoding="utf-8")

    mgr.startup_sweep()

    assert not orphan.exists(), "orphan install dir should have been removed"


def test_startup_sweep_marks_missing_install_path_as_error(tmp_path: Path):
    mgr = _make_mgr(tmp_path)
    missing_path = tmp_path / "plugins" / "gone-1.0.0"
    mgr.registry.upsert(
        name="gone", version="1.0.0", status="installed", score=90,
        permissions=[], install_path=str(missing_path),
    )
    assert not missing_path.exists()

    mgr.startup_sweep()

    row = mgr.registry.get("gone")
    assert row is not None
    assert row.status == "error"
    assert row.last_error == "install directory missing"


def test_startup_sweep_keeps_plugins_with_dashes_in_name(tmp_path: Path):
    """A plugin named 'my-plugin-x' installs to 'my-plugin-x-1.0.0'.
    rsplit('-', 1) keeps the name stem intact, so this should NOT be orphaned."""
    mgr = _make_mgr(tmp_path)
    install_dir = tmp_path / "plugins" / "my-plugin-x-1.0.0"
    install_dir.mkdir(parents=True)
    (install_dir / "plugin.json").write_text("{}", encoding="utf-8")
    mgr.registry.upsert(
        name="my-plugin-x", version="1.0.0", status="installed", score=90,
        permissions=[], install_path=str(install_dir),
    )

    mgr.startup_sweep()

    assert install_dir.exists(), (
        "plugin dir with dashes was incorrectly orphan-swept"
    )
    row = mgr.registry.get("my-plugin-x")
    assert row is not None and row.status == "installed"
