"""RuntimeSandbox — host side of the plugin subprocess protocol.

Spawns `python -m shadowdev.plugin_host`, performs handshake, exposes a simple
async API: start / stop / invoke / tool_names.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxError(RuntimeError):
    pass


class RuntimeSandbox:
    def __init__(
        self,
        plugin_dir: str | Path,
        permissions: list[str],
        *,
        call_timeout_s: float = 30.0,
    ) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.permissions = list(permissions)
        self.call_timeout_s = call_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._tools: list[str] = []
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "shadowdev.plugin_host",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ack = await self._rpc("handshake", {
            "plugin_dir": str(self.plugin_dir),
            "permissions": self.permissions,
        })
        self._tools = [t["name"] for t in ack.get("tools", [])]

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            await asyncio.wait_for(self._rpc("shutdown", {}), timeout=2.0)
        except Exception:
            pass
        if self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    def tool_names(self) -> list[str]:
        return list(self._tools)

    async def invoke(self, name: str, args: dict):
        try:
            return await asyncio.wait_for(
                self._rpc("tool.invoke", {"name": name, "args": args}),
                timeout=self.call_timeout_s,
            )
        except asyncio.TimeoutError:
            if self._proc and self._proc.returncode is None:
                self._proc.kill()
                await self._proc.wait()
            self._proc = None
            raise TimeoutError(
                f"plugin call {name!r} exceeded {self.call_timeout_s}s timeout"
            )

    async def _rpc(self, method: str, params: dict):
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        async with self._lock:
            rid = self._next_id
            self._next_id += 1
            body = json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "method": method, "params": params,
            }).encode("utf-8")
            self._proc.stdin.write(struct.pack(">I", len(body)) + body)
            await self._proc.stdin.drain()

            hdr = await self._proc.stdout.readexactly(4)
            (n,) = struct.unpack(">I", hdr)
            body = await self._proc.stdout.readexactly(n)
            reply = json.loads(body.decode("utf-8"))
            if "error" in reply:
                err = reply["error"]
                raise SandboxError(f"[{err['code']}] {err['message']}")
            return reply.get("result")
