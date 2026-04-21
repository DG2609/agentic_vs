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
