"""PluginManager — public facade for the plugin system.

Everything outside agent/plugins/ imports only this class.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from agent.plugins.auditor import QualityAuditor
from agent.plugins.hub_scout import HubScout
from agent.plugins.installer import Installer
from agent.plugins.registry_db import PluginRegistryDB
from agent.plugins.sandbox import RuntimeSandbox
from agent.plugins.types import InstalledPlugin, PluginMeta, QualityReport

logger = logging.getLogger(__name__)


class PluginError(RuntimeError):
    pass


class PluginManager:
    def __init__(
        self,
        *,
        hub_index_url: str,
        install_root: str | Path,
        temp_root: str | Path,
        db_path: str | Path,
        cache_dir: str | Path,
    ) -> None:
        self.hub = HubScout(index_url=hub_index_url, cache_dir=cache_dir)
        self.installer = Installer(install_root=install_root, temp_root=temp_root)
        self.auditor = QualityAuditor()
        self.registry = PluginRegistryDB(db_path)
        self._sandboxes: dict[str, RuntimeSandbox] = {}

    async def search(self, q: str, *, category: str | None = None) -> list[PluginMeta]:
        return await self.hub.search(q, category=category)

    async def inspect(self, name: str) -> PluginMeta | None:
        return await self.hub.inspect(name)

    async def audit(self, name: str, *, version: str | None = None) -> QualityReport:
        meta = await self.hub.inspect(name)
        if meta is None:
            raise PluginError(f"plugin not found in hub: {name}")
        stage = await self.installer.download_and_extract(meta)
        try:
            return await self.auditor.audit(stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    async def install(
        self,
        name: str,
        *,
        version: str | None = None,
        permissions: list[str] | None = None,
        force: bool = False,
    ) -> InstalledPlugin:
        meta = await self.hub.inspect(name)
        if meta is None:
            raise PluginError(f"plugin not found in hub: {name}")
        stage = await self.installer.download_and_extract(meta)
        try:
            report = await self.auditor.audit(stage)
            if report.blocked and not force:
                raise PluginError(
                    f"audit blocked install (score={report.score}, "
                    f"blockers={[b.rule for b in report.blockers]})"
                )
            final = self.installer.promote(stage, name=name, version=meta.version)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        self.registry.upsert(
            name=name, version=meta.version, status="installed",
            score=report.score, permissions=permissions or [],
            install_path=str(final),
        )
        result = self.registry.get(name)
        assert result is not None
        return result

    async def uninstall(self, name: str) -> None:
        row = self.registry.get(name)
        self.registry.delete(name)
        await self.unload(name)
        if row and row.install_path:
            shutil.rmtree(row.install_path, ignore_errors=True)

    async def load_runtime(self, name: str) -> list:
        row = self.registry.get(name)
        if row is None:
            raise PluginError(f"plugin not installed: {name}")
        if name in self._sandboxes:
            await self.unload(name)
        sb = RuntimeSandbox(plugin_dir=row.install_path, permissions=row.permissions)
        try:
            await sb.start()
        except Exception as e:
            self.registry.upsert(
                name=name, version=row.version, status="error", score=row.score,
                permissions=row.permissions, install_path=row.install_path,
                last_error=str(e),
            )
            raise
        self._sandboxes[name] = sb
        return [self._make_proxy_tool(sb, tname) for tname in sb.tool_names()]

    async def unload(self, name: str) -> None:
        sb = self._sandboxes.pop(name, None)
        if sb is not None:
            await sb.stop()

    async def reload(self, name: str) -> list:
        await self.unload(name)
        return await self.load_runtime(name)

    def list_installed(self) -> list[InstalledPlugin]:
        return self.registry.list_all()

    @staticmethod
    def _make_proxy_tool(sb: RuntimeSandbox, tname: str):
        return _ProxyTool(sb=sb, name=tname, description=f"Proxied plugin tool: {tname}")


class _ProxyArgs(BaseModel):
    """Permissive args schema — plugin-side tool validates its own args."""
    model_config = ConfigDict(extra="allow")


class _ProxyTool(BaseTool):
    """LangChain tool that forwards invocations to a sandboxed plugin.

    Bypasses LangChain's pydantic validation since plugin tools have
    arbitrary signatures; the plugin-side tool does its own validation.
    """
    args_schema: type[BaseModel] = _ProxyArgs
    sb: RuntimeSandbox

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def invoke(self, input, config=None, **kwargs):
        args = input if isinstance(input, dict) else {"input": input}
        return asyncio.get_event_loop().run_until_complete(self.sb.invoke(self.name, args))

    async def ainvoke(self, input, config=None, **kwargs):
        args = input if isinstance(input, dict) else {"input": input}
        return await self.sb.invoke(self.name, args)

    def _run(self, *args, **kwargs):
        return asyncio.get_event_loop().run_until_complete(self.sb.invoke(self.name, kwargs))

    async def _arun(self, *args, **kwargs):
        return await self.sb.invoke(self.name, kwargs)
