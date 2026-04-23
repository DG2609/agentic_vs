"""Installer — download, SHA256 verify, safe extract, atomic promote.

Never imports plugin code. Hard-blocks path traversal or absolute paths
in tar members.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import os
import shutil
import tarfile
import uuid
from pathlib import Path

import aiohttp

from agent.plugins.types import PluginMeta

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_S = 60
_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024            # 50 MB compressed
_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024       # 200 MB uncompressed (zip-bomb guard)


class InstallerError(Exception):
    """Base class for installer errors."""


class IntegrityError(InstallerError):
    """SHA256 mismatch or signature failure."""


class BadArchiveError(InstallerError):
    """Tarball contains a path-traversal or absolute-path member."""


class Installer:
    def __init__(
        self,
        install_root: str | Path,
        temp_root: str | Path,
        *,
        hub_public_key: bytes | None = None,
    ) -> None:
        self.install_root = Path(install_root)
        self.temp_root = Path(temp_root)
        self.hub_public_key = hub_public_key
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    async def download_and_extract(self, meta: PluginMeta) -> Path:
        stage = self.temp_root / f"pending-{uuid.uuid4().hex[:8]}"
        stage.mkdir(parents=True, exist_ok=True)
        try:
            blob = await self._download(meta.url)
            actual = hashlib.sha256(blob).hexdigest()
            if actual != meta.sha256:
                raise IntegrityError(
                    f"SHA256 mismatch: expected {meta.sha256[:12]}..., got {actual[:12]}..."
                )
            if meta.signature:
                if self.hub_public_key is None:
                    raise IntegrityError(
                        "plugin declared a signature but no hub public key is configured"
                    )
                _verify_signature(self.hub_public_key, meta.sha256, meta.signature)
            self._safe_extract(blob, stage)
            return stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def promote(self, stage: Path, *, name: str, version: str) -> Path:
        final = self.install_root / f"{name}-{version}"
        for child in self.install_root.iterdir():
            if child.name.startswith(f"{name}-") and child != final:
                shutil.rmtree(child, ignore_errors=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(stage, final)
        return final

    async def _download(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=_DOWNLOAD_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                length = int(resp.headers.get("Content-Length", "0"))
                if length and length > _MAX_ARTIFACT_BYTES:
                    raise InstallerError(f"artifact too large: {length} bytes")
                data = await resp.read()
                if len(data) > _MAX_ARTIFACT_BYTES:
                    raise InstallerError(f"artifact too large: {len(data)} bytes")
                return data

    @staticmethod
    def _safe_extract(blob: bytes, dest: Path) -> None:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            total = 0
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise BadArchiveError(f"unsafe member: {member.name!r}")
                if member.islnk() or member.issym():
                    raise BadArchiveError(f"symlink rejected: {member.name!r}")
                total += max(member.size, 0)
                if total > _MAX_UNCOMPRESSED_BYTES:
                    raise BadArchiveError(
                        f"archive uncompresses to >{_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB"
                    )
            # filter="data" — the Python 3.12+ default for 3.14; rejects
            # absolute paths, traversal, symlinks, and strips owner/mode bits.
            try:
                tar.extractall(dest, filter="data")
            except TypeError:
                # Python < 3.12 — fall back to un-filtered extract (checks above still apply)
                tar.extractall(dest)


def _verify_signature(pubkey_bytes: bytes, sha256_hex: str, signature_str: str) -> None:
    """Verify an ed25519 detached signature over the sha256 digest (binary).

    `signature_str` may be hex or base64. Raises IntegrityError on mismatch.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except Exception as e:
        raise IntegrityError(f"cryptography not available: {e}")

    # Decode signature — accept hex or base64.
    try:
        sig = binascii.unhexlify(signature_str)
    except (binascii.Error, ValueError):
        try:
            sig = base64.b64decode(signature_str, validate=True)
        except binascii.Error as e:
            raise IntegrityError(f"signature is neither hex nor base64: {e}")

    digest = bytes.fromhex(sha256_hex)
    try:
        pk = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
    except Exception as e:
        raise IntegrityError(f"invalid ed25519 public key: {e}")
    try:
        pk.verify(sig, digest)
    except InvalidSignature:
        raise IntegrityError("ed25519 signature verification failed")
