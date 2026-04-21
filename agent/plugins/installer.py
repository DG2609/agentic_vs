"""Installer — download, SHA256 verify, safe extract, atomic promote.

Never imports plugin code. Hard-blocks path traversal or absolute paths
in tar members.
"""
from __future__ import annotations

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
_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024   # 50 MB hard cap


class InstallerError(Exception):
    """Base class for installer errors."""


class IntegrityError(InstallerError):
    """SHA256 mismatch or signature failure."""


class BadArchiveError(InstallerError):
    """Tarball contains a path-traversal or absolute-path member."""


class Installer:
    def __init__(self, install_root: str | Path, temp_root: str | Path) -> None:
        self.install_root = Path(install_root)
        self.temp_root = Path(temp_root)
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
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise BadArchiveError(f"unsafe member: {member.name!r}")
                if member.islnk() or member.issym():
                    raise BadArchiveError(f"symlink rejected: {member.name!r}")
            tar.extractall(dest)
