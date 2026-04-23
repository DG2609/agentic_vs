import pytest
from agent.plugins.registry_db import PluginRegistryDB


@pytest.fixture
def db(tmp_path):
    return PluginRegistryDB(tmp_path / "plugins.db")


def test_upsert_and_get(db):
    db.upsert(
        name="demo", version="1.0.0", status="installed",
        score=85, permissions=["net.http"], install_path="/a/b",
    )
    p = db.get("demo")
    assert p is not None
    assert p.name == "demo"
    assert p.score == 85
    assert p.permissions == ["net.http"]


def test_get_missing_returns_none(db):
    assert db.get("nope") is None


def test_list_all(db):
    db.upsert(name="a", version="1", status="installed", score=80, permissions=[], install_path="/a")
    db.upsert(name="b", version="1", status="error",     score=0,  permissions=[], install_path="/b")
    rows = db.list_all()
    assert {r.name for r in rows} == {"a", "b"}


def test_delete(db):
    db.upsert(name="x", version="1", status="installed", score=80, permissions=[], install_path="/x")
    assert db.delete("x") is True
    assert db.get("x") is None
    assert db.delete("x") is False


def test_corrupt_db_rebuilds(tmp_path):
    path = tmp_path / "plugins.db"
    path.write_bytes(b"not a sqlite file")
    db = PluginRegistryDB(path)
    assert db.list_all() == []
    assert (tmp_path / "plugins.db.bak").exists()


def test_upsert_updates_existing(db):
    db.upsert(name="demo", version="1", status="installed", score=70, permissions=[], install_path="/a")
    db.upsert(name="demo", version="2", status="installed", score=90, permissions=["env"], install_path="/a")
    p = db.get("demo")
    assert p.version == "2"
    assert p.score == 90
    assert p.permissions == ["env"]


def test_raw_report_round_trip(db):
    report = {"score": 85, "blocked": False, "blockers": [], "issues": [
        {"rule": "W1", "message": "msg", "severity": "low", "file": "a.py", "line": 1},
    ]}
    db.upsert(
        name="r", version="1", status="installed", score=85,
        permissions=[], install_path="/r", raw_report=report,
    )
    assert db.get_raw_report("r") == report


def test_raw_report_missing_returns_none(db):
    db.upsert(
        name="nr", version="1", status="installed", score=85,
        permissions=[], install_path="/nr",
    )
    assert db.get_raw_report("nr") is None


def test_raw_report_preserved_across_status_update(db):
    """Updating status must not clobber a previously persisted report."""
    report = {"score": 80, "blocked": False, "blockers": [], "issues": []}
    db.upsert(name="p", version="1", status="installed", score=80,
              permissions=[], install_path="/p", raw_report=report)
    # Re-upsert without passing raw_report — must be retained.
    db.upsert(name="p", version="1", status="error", score=80,
              permissions=[], install_path="/p", last_error="boom")
    assert db.get_raw_report("p") == report
