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


_HANDSHAKE_TIMEOUT_S = 10.0
_SHUTDOWN_TIMEOUT_S = 2.0


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
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "shadowdev.plugin_host",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Drain stderr in the background so a chatty plugin can't fill the
        # pipe buffer (~64 KB on Windows) and hang the next stdin write.
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            ack = await asyncio.wait_for(
                self._rpc("handshake", {
                    "plugin_dir": str(self.plugin_dir),
                    "permissions": self.permissions,
                }),
                timeout=_HANDSHAKE_TIMEOUT_S,
            )
        except BaseException:
            await self._kill_proc()
            raise
        self._tools = [t["name"] for t in ack.get("tools", [])]

    async def stop(self) -> None:
        if self._proc is None:
            return
        try:
            await asyncio.wait_for(self._rpc("shutdown", {}), timeout=_SHUTDOWN_TIMEOUT_S)
        except Exception:
            pass
        await self._kill_proc()

    async def _kill_proc(self) -> None:
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=_SHUTDOWN_TIMEOUT_S)
            except asyncio.TimeoutError:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await self._proc.wait()
                except Exception:
                    pass
        self._proc = None
        self._stderr_task = None

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                try:
                    logger.debug("[plugin stderr] %s", line.decode("utf-8", "replace").rstrip())
                except Exception:
                    pass
        except asyncio.CancelledError:
            return
        except Exception:
            return

    def tool_names(self) -> list[str]:
        return list(self._tools)

    async def invoke(self, name: str, args: dict):
        try:
            return await asyncio.wait_for(
                self._rpc("tool.invoke", {"name": name, "args": args}),
                timeout=self.call_timeout_s,
            )
        except asyncio.TimeoutError:
            await self._kill_proc()
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

            try:
                hdr = await self._proc.stdout.readexactly(4)
            except asyncio.IncompleteReadError as e:
                raise SandboxError(
                    f"plugin host closed stdout mid-reply (read {len(e.partial)}/4 header bytes)"
                ) from e
            (n,) = struct.unpack(">I", hdr)
            try:
                body = await self._proc.stdout.readexactly(n)
            except asyncio.IncompleteReadError as e:
                raise SandboxError(
                    f"plugin host closed stdout mid-reply (read {len(e.partial)}/{n} body bytes)"
                ) from e
            reply = json.loads(body.decode("utf-8"))
            if "error" in reply:
                err = reply["error"]
                raise SandboxError(f"[{err['code']}] {err['message']}")
            return reply.get("result")
