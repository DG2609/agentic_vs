import hashlib
import io
import tarfile
import pytest

from agent.plugins.manager import PluginManager


def _make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_GOOD_MANIFEST = b'{"name":"demo","version":"1.0.0","tools":["say_hi"],"permissions":[],"entry":"demo.tools"}'
_GOOD_TOOLS = b'''from langchain_core.tools import tool

@tool
def say_hi(name: str) -> str:
    """Say hi."""
    return f"hi {name}"

__skill_tools__ = [say_hi]
'''


@pytest.mark.asyncio
async def test_happy_path_install_load_invoke(tmp_path, fake_hub):
    blob = _make_tar({
        "plugin.json": _GOOD_MANIFEST,
        "demo/__init__.py": b"",
        "demo/tools.py": _GOOD_TOOLS,
    })
    fake_hub["artefacts"]["demo-1.0.0.tar.gz"] = blob
    from agent.plugins.hub_scout import HubScout
    orig_parse = HubScout._parse

    def patched_parse(data):
        metas = orig_parse(data)
        for m in metas:
            if m.name == "demo":
                m.sha256 = _sha(blob)
        return metas
    HubScout._parse = staticmethod(patched_parse)  # type: ignore[assignment]

    try:
        mgr = PluginManager(
            hub_index_url=fake_hub["url"],
            install_root=tmp_path / "plugins",
            temp_root=tmp_path / "tmp",
            db_path=tmp_path / "plugins.db",
            cache_dir=tmp_path / "cache",
        )

        report = await mgr.audit("demo")
        assert not report.blocked

        installed = await mgr.install("demo", version="1.0.0", permissions=[])
        assert installed.name == "demo"
        assert installed.status == "installed"

        tools = await mgr.load_runtime("demo")
        assert any(t.name == "say_hi" for t in tools)

        result = await tools[0].ainvoke({"name": "abc"})
        assert result == "hi abc"

        await mgr.unload("demo")
    finally:
        HubScout._parse = staticmethod(orig_parse)  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_audit_blocker_halts_install(tmp_path, fake_hub):
    bad_tools = b"import os\nos.system('echo hi')\n__skill_tools__ = []\n"
    blob = _make_tar({
        "plugin.json": b'{"name":"bad","version":"1.0.0","tools":[],"permissions":[],"entry":"bad.tools"}',
        "bad/__init__.py": b"",
        "bad/tools.py": bad_tools,
    })
    fake_hub["artefacts"]["bad-1.0.0.tar.gz"] = blob
    from agent.plugins.hub_scout import HubScout
    orig_parse = HubScout._parse

    def patched_parse(data):
        metas = orig_parse(data)
        from agent.plugins.types import PluginMeta
        host = fake_hub["server"].host
        port = fake_hub["server"].port
        metas.append(PluginMeta(
            name="bad", version="1.0.0",
            url=f"http://{host}:{port}/artefacts/bad-1.0.0.tar.gz",
            sha256=_sha(blob), permissions=[],
        ))
        return metas
    HubScout._parse = staticmethod(patched_parse)  # type: ignore[assignment]
    try:
        mgr = PluginManager(
            hub_index_url=fake_hub["url"],
            install_root=tmp_path / "plugins",
            temp_root=tmp_path / "tmp",
            db_path=tmp_path / "plugins.db",
            cache_dir=tmp_path / "cache",
        )
        with pytest.raises(Exception) as ei:
            await mgr.install("bad", version="1.0.0", permissions=[])
        assert "block" in str(ei.value).lower() or "audit" in str(ei.value).lower()
        assert mgr.registry.get("bad") is None
    finally:
        HubScout._parse = staticmethod(orig_parse)  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_uninstall_removes_row_and_files(tmp_path, fake_hub):
    blob = _make_tar({
        "plugin.json": _GOOD_MANIFEST,
        "demo/__init__.py": b"",
        "demo/tools.py": _GOOD_TOOLS,
    })
    (tmp_path / "plugins" / "demo-1.0.0").mkdir(parents=True)
    import tarfile as _t
    with _t.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
        tar.extractall(tmp_path / "plugins" / "demo-1.0.0")

    mgr = PluginManager(
        hub_index_url=fake_hub["url"],
        install_root=tmp_path / "plugins",
        temp_root=tmp_path / "tmp",
        db_path=tmp_path / "plugins.db",
        cache_dir=tmp_path / "cache",
    )
    mgr.registry.upsert(
        name="demo", version="1.0.0", status="installed", score=90,
        permissions=[], install_path=str(tmp_path / "plugins" / "demo-1.0.0"),
    )

    await mgr.uninstall("demo")
    assert mgr.registry.get("demo") is None
    assert not (tmp_path / "plugins" / "demo-1.0.0").exists()
