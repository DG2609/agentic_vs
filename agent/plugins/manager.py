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
        hub_public_key: bytes | None = None,
    ) -> None:
        self.install_root = Path(install_root)
        self.hub = HubScout(index_url=hub_index_url, cache_dir=cache_dir)
        self.installer = Installer(
            install_root=install_root, temp_root=temp_root,
            hub_public_key=hub_public_key,
        )
        self.auditor = QualityAuditor()
        self.registry = PluginRegistryDB(db_path)
        self._sandboxes: dict[str, RuntimeSandbox] = {}

    def startup_sweep(self) -> None:
        """Reconcile on-disk install dirs with the registry DB.

        - Removes install dirs that have no corresponding DB row (orphans).
        - Marks DB rows whose install_path is missing as status='error'.
        """
        rows = {r.name: r for r in self.registry.list_all()}
        # Orphan dirs: exist on disk but no matching DB row
        if self.install_root.exists():
            for entry in self.install_root.iterdir():
                if not entry.is_dir():
                    continue
                # install dirs are named "<name>-<version>"
                stem = entry.name.rsplit("-", 1)[0]
                if stem not in rows:
                    shutil.rmtree(entry, ignore_errors=True)
        # Missing dirs: DB says installed but dir gone
        for name, r in rows.items():
            if r.install_path and not Path(r.install_path).exists():
                self.registry.upsert(
                    name=name, version=r.version, status="error",
                    score=r.score, permissions=r.permissions,
                    install_path=r.install_path,
                    last_error="install directory missing",
                )

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
            raw_report={
                "score": report.score,
                "blocked": report.blocked,
                "issues": [
                    {"rule": i.rule, "message": i.message, "severity": i.severity,
                     "file": i.file, "line": i.line}
                    for i in report.issues
                ],
                "blockers": [
                    {"rule": i.rule, "message": i.message, "severity": i.severity,
                     "file": i.file, "line": i.line}
                    for i in report.blockers
                ],
            },
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
        return [self._make_proxy_tool(sb, d) for d in sb.tool_descriptors()]

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
    def _make_proxy_tool(sb: RuntimeSandbox, descriptor: dict):
        tname = descriptor.get("name", "")
        tdesc = descriptor.get("description") or f"Proxied plugin tool: {tname}"
        schema = descriptor.get("schema")
        args_model = _build_args_model(tname, schema) if schema else _ProxyArgs
        return _ProxyTool(
            sb=sb, name=tname, description=tdesc, args_schema=args_model,
        )


def _build_args_model(tool_name: str, schema: dict) -> type[BaseModel]:
    """Construct a permissive pydantic model from the plugin's JSON schema.

    We don't try to recreate field-level validation — the plugin-side tool
    still does that. What we want is a *description* the LLM can read so it
    knows which kwargs to pass.
    """
    try:
        from pydantic import Field, create_model
    except Exception:
        return _ProxyArgs

    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])

    fields = {}
    type_map = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }
    for pname, prop in props.items():
        py_type = type_map.get((prop or {}).get("type", ""), object)
        description = (prop or {}).get("description", "")
        default = ... if pname in required else (prop or {}).get("default", None)
        fields[pname] = (py_type, Field(default=default, description=description))

    if not fields:
        return _ProxyArgs
    try:
        model = create_model(f"_PluginArgs_{tool_name}", **fields)  # type: ignore[arg-type]
        model.model_config = ConfigDict(extra="allow")
        return model
    except Exception:
        return _ProxyArgs


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
        return _run_sync(self.sb.invoke(self.name, args))

    async def ainvoke(self, input, config=None, **kwargs):
        args = input if isinstance(input, dict) else {"input": input}
        return await self.sb.invoke(self.name, args)

    def _run(self, *args, **kwargs):
        return _run_sync(self.sb.invoke(self.name, kwargs))

    async def _arun(self, *args, **kwargs):
        return await self.sb.invoke(self.name, kwargs)


def _run_sync(coro):
    """Run a coroutine from a sync context without trampling on a live loop.

    Three cases:
      1. No running loop in current thread → asyncio.run.
      2. A loop is running in *another* thread → schedule + wait via run_coroutine_threadsafe.
      3. A loop is running in the *current* thread → raise. Sync invoke is unsafe
         from async code — the caller should use `ainvoke` instead.
    """
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None

    if current is None:
        return asyncio.run(coro)

    # Loop is running in this thread — sync invoke would deadlock.
    # Try to find a background loop on another thread if available.
    bg = _get_background_loop()
    if bg is not None and bg is not current:
        fut = asyncio.run_coroutine_threadsafe(coro, bg)
        return fut.result()
    raise RuntimeError(
        "Cannot invoke plugin tool synchronously from inside a running event loop; "
        "use `await tool.ainvoke(...)` instead."
    )


_BG_LOOP: asyncio.AbstractEventLoop | None = None


def _get_background_loop() -> asyncio.AbstractEventLoop | None:
    """Return a user-supplied background loop, if one has been registered."""
    return _BG_LOOP


def set_background_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Register a background asyncio loop that sync plugin invokes can dispatch to.

    Intended for hosts that run the plugin manager inside an aiohttp/LangGraph
    loop but expose sync entry points to LangChain.
    """
    global _BG_LOOP
    _BG_LOOP = loop


# ── Singleton binding ─────────────────────────────────────────
# The agent graph reaches into this module to find the live manager
# without creating an import cycle with server.main.

_SINGLETON: PluginManager | None = None


def set_singleton(mgr: PluginManager | None) -> None:
    global _SINGLETON
    _SINGLETON = mgr


def get_singleton() -> PluginManager | None:
    return _SINGLETON
