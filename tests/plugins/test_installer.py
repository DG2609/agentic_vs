import hashlib
import io
import tarfile
import pytest

from agent.plugins.installer import Installer, IntegrityError, BadArchiveError


def _make_tarball(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.mark.asyncio
async def test_download_and_promote(tmp_path, fake_hub):
    blob = _make_tarball({
        "demo/plugin.json": b'{"name":"demo","version":"1.0.0","tools":[],"permissions":[]}',
        "demo/tools.py": b"__skill_tools__ = []\n",
    })
    fake_hub["artefacts"]["demo-1.0.0.tar.gz"] = blob
    digest = _sha256(blob)

    install_root = tmp_path / "plugins"
    inst = Installer(install_root=install_root, temp_root=tmp_path / "tmp")

    from agent.plugins.types import PluginMeta
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="demo", version="1.0.0",
        url=f"http://{host}:{port}/artefacts/demo-1.0.0.tar.gz",
        sha256=digest,
    )
    staged = await inst.download_and_extract(meta)
    assert (staged / "demo" / "plugin.json").is_file()

    final = inst.promote(staged, name="demo", version="1.0.0")
    assert final.is_dir()
    assert (final / "demo" / "plugin.json").is_file()


@pytest.mark.asyncio
async def test_sha_mismatch_raises(tmp_path, fake_hub):
    blob = _make_tarball({"a.py": b"x=1"})
    fake_hub["artefacts"]["bad.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="bad", version="1", sha256="0" * 64,
        url=f"http://{host}:{port}/artefacts/bad.tar.gz",
    )
    with pytest.raises(IntegrityError):
        await inst.download_and_extract(meta)


@pytest.mark.asyncio
async def test_rejects_path_traversal_member(tmp_path, fake_hub):
    blob = _make_tarball({"../../etc/evil": b"x"})
    fake_hub["artefacts"]["evil.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="evil", version="1", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/evil.tar.gz",
    )
    with pytest.raises(BadArchiveError):
        await inst.download_and_extract(meta)


@pytest.mark.asyncio
async def test_rejects_absolute_path_member(tmp_path, fake_hub):
    blob = _make_tarball({"/etc/evil": b"x"})
    fake_hub["artefacts"]["abs.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="abs", version="1", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/abs.tar.gz",
    )
    with pytest.raises(BadArchiveError):
        await inst.download_and_extract(meta)


@pytest.mark.asyncio
async def test_rejects_uncompressed_bomb(tmp_path, fake_hub, monkeypatch):
    """A tarball that decompresses beyond the cap must be rejected."""
    import agent.plugins.installer as inst_mod
    # Lower the cap so the test stays fast.
    monkeypatch.setattr(inst_mod, "_MAX_UNCOMPRESSED_BYTES", 1024 * 1024)
    # Member declares 10 MB — exceeds 1 MB cap.
    members = {"bomb.bin": b"\x00" * (10 * 1024 * 1024)}
    blob = _make_tarball(members)
    fake_hub["artefacts"]["bomb.tar.gz"] = blob
    host = fake_hub["server"].host
    port = fake_hub["server"].port

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    from agent.plugins.types import PluginMeta
    meta = PluginMeta(
        name="bomb", version="1", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/bomb.tar.gz",
    )
    with pytest.raises(BadArchiveError) as ei:
        await inst.download_and_extract(meta)
    assert "uncompress" in str(ei.value).lower()


def test_promote_idempotent_replace(tmp_path):
    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    s1 = tmp_path / "s1"
    (s1 / "a").mkdir(parents=True)
    (s1 / "a" / "x.txt").write_text("v1")
    s2 = tmp_path / "s2"
    (s2 / "a").mkdir(parents=True)
    (s2 / "a" / "x.txt").write_text("v2")

    final1 = inst.promote(s1, name="p", version="1")
    assert (final1 / "a" / "x.txt").read_text() == "v1"
    final2 = inst.promote(s2, name="p", version="2")
    assert (final2 / "a" / "x.txt").read_text() == "v2"
    assert not final1.exists() or final1 == final2
