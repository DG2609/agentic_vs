"""Hub index fetcher. 10-min memory cache, 7-day disk fallback.

Designed so that network failure never blocks the user — a stale cache is
preferred to a hard error.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

from agent.plugins.types import PluginMeta

logger = logging.getLogger(__name__)

_MEM_TTL_S = 600              # 10 minutes
_DISK_MAX_STALE_S = 7 * 86400 # 7 days


class HubScout:
    def __init__(
        self,
        index_url: str,
        cache_dir: str | Path,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._index_url = index_url
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "index.cache.json"
        self._timeout_s = timeout_s
        self._mem_cache: list[PluginMeta] | None = None
        self._mem_cache_at: float = 0.0
        self._lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
    ) -> list[PluginMeta]:
        index = await self._get_index()
        q = query.lower().strip()
        out: list[PluginMeta] = []
        for p in index:
            if category and p.category != category:
                continue
            if q and q not in p.name.lower() and q not in p.description.lower():
                continue
            out.append(p)
        return out

    async def inspect(self, name: str) -> PluginMeta | None:
        index = await self._get_index()
        for p in index:
            if p.name == name:
                return p
        return None

    async def _get_index(self) -> list[PluginMeta]:
        async with self._lock:
            now = time.time()
            if self._mem_cache is not None and (now - self._mem_cache_at) < _MEM_TTL_S:
                return self._mem_cache

            try:
                data = await self._fetch_remote()
                self._write_disk_cache(data)
                self._mem_cache = self._parse(data)
                self._mem_cache_at = now
                return self._mem_cache
            except Exception as e:
                logger.warning("Hub fetch failed (%s); falling back to disk cache", e)
                data = self._read_disk_cache()
                if data is None:
                    raise
                self._mem_cache = self._parse(data)
                self._mem_cache_at = now
                return self._mem_cache

    async def _fetch_remote(self) -> dict:
        timeout = aiohttp.ClientTimeout(total=self._timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self._index_url) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _read_disk_cache(self) -> dict | None:
        if not self._cache_file.exists():
            return None
        age = time.time() - self._cache_file.stat().st_mtime
        if age > _DISK_MAX_STALE_S:
            return None
        try:
            return json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_disk_cache(self, data: dict) -> None:
        try:
            self._cache_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            logger.debug("Hub cache write failed: %s", e)

    @staticmethod
    def _parse(data: dict) -> list[PluginMeta]:
        out: list[PluginMeta] = []
        for p in data.get("plugins", []):
            out.append(
                PluginMeta(
                    name=p["name"],
                    version=p["version"],
                    url=p["url"],
                    sha256=p["sha256"],
                    author=p.get("author", ""),
                    description=p.get("description", ""),
                    category=p.get("category", ""),
                    tags=p.get("tags", []),
                    permissions=p.get("permissions", []),
                    tool_count=p.get("tool_count", 0),
                    size_bytes=p.get("size_bytes", 0),
                    signature=p.get("signature"),
                )
            )
        return out
