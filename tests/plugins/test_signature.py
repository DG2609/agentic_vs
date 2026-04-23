"""Tests for ed25519 signature verification in the Installer."""
import hashlib
import io
import tarfile
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from agent.plugins.installer import Installer, IntegrityError
from agent.plugins.types import PluginMeta


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


def _pub_bytes(priv: Ed25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


@pytest.mark.asyncio
async def test_valid_ed25519_signature_accepted(tmp_path, fake_hub):
    priv = Ed25519PrivateKey.generate()
    blob = _make_tarball({"x.py": b"__skill_tools__ = []\n"})
    fake_hub["artefacts"]["signed.tar.gz"] = blob
    digest_hex = _sha256(blob)
    sig_hex = priv.sign(bytes.fromhex(digest_hex)).hex()

    inst = Installer(
        install_root=tmp_path / "p", temp_root=tmp_path / "tmp",
        hub_public_key=_pub_bytes(priv),
    )
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="signed", version="1.0.0", sha256=digest_hex,
        url=f"http://{host}:{port}/artefacts/signed.tar.gz",
        signature=sig_hex,
    )
    stage = await inst.download_and_extract(meta)
    assert (stage / "x.py").is_file()


@pytest.mark.asyncio
async def test_forged_signature_rejected(tmp_path, fake_hub):
    priv_legit = Ed25519PrivateKey.generate()
    priv_attacker = Ed25519PrivateKey.generate()
    blob = _make_tarball({"x.py": b"__skill_tools__ = []\n"})
    fake_hub["artefacts"]["forged.tar.gz"] = blob
    digest_hex = _sha256(blob)
    # signature from a different key
    sig_hex = priv_attacker.sign(bytes.fromhex(digest_hex)).hex()

    inst = Installer(
        install_root=tmp_path / "p", temp_root=tmp_path / "tmp",
        hub_public_key=_pub_bytes(priv_legit),
    )
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="forged", version="1.0.0", sha256=digest_hex,
        url=f"http://{host}:{port}/artefacts/forged.tar.gz",
        signature=sig_hex,
    )
    with pytest.raises(IntegrityError) as ei:
        await inst.download_and_extract(meta)
    assert "verification failed" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_signature_declared_but_no_hub_key_rejected(tmp_path, fake_hub):
    blob = _make_tarball({"x.py": b"__skill_tools__ = []\n"})
    fake_hub["artefacts"]["needs_key.tar.gz"] = blob
    digest_hex = _sha256(blob)

    inst = Installer(
        install_root=tmp_path / "p", temp_root=tmp_path / "tmp",
        hub_public_key=None,
    )
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="nk", version="1.0.0", sha256=digest_hex,
        url=f"http://{host}:{port}/artefacts/needs_key.tar.gz",
        signature="00" * 64,
    )
    with pytest.raises(IntegrityError) as ei:
        await inst.download_and_extract(meta)
    assert "no hub public key" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_no_signature_no_pubkey_still_installs(tmp_path, fake_hub):
    """Backwards-compat: SHA256-only model still works when neither side has keys."""
    blob = _make_tarball({"x.py": b"__skill_tools__ = []\n"})
    fake_hub["artefacts"]["plain.tar.gz"] = blob

    inst = Installer(install_root=tmp_path / "p", temp_root=tmp_path / "tmp")
    host = fake_hub["server"].host
    port = fake_hub["server"].port
    meta = PluginMeta(
        name="plain", version="1.0.0", sha256=_sha256(blob),
        url=f"http://{host}:{port}/artefacts/plain.tar.gz",
    )
    stage = await inst.download_and_extract(meta)
    assert (stage / "x.py").is_file()
