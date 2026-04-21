"""Tests that exercise the plugin_host binary via a real subprocess."""
import asyncio
import json
import struct
import sys
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


async def _rpc_send(writer: asyncio.StreamWriter, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


async def _rpc_recv(reader: asyncio.StreamReader) -> dict:
    hdr = await reader.readexactly(4)
    (n,) = struct.unpack(">I", hdr)
    body = await reader.readexactly(n)
    return json.loads(body.decode("utf-8"))


async def _spawn_host(plugin_dir: Path, perms: list[str]):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "shadowdev.plugin_host",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 0, "method": "handshake",
        "params": {"plugin_dir": str(plugin_dir), "permissions": perms},
    })
    ack = await _rpc_recv(proc.stdout)
    assert ack.get("result", {}).get("ok") is True, ack
    return proc


@pytest.mark.asyncio
async def test_list_tools():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {"jsonrpc": "2.0", "id": 1, "method": "tool.list"})
    resp = await _rpc_recv(proc.stdout)
    assert "result" in resp
    names = {t["name"] for t in resp["result"]}
    assert "say_hi" in names
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_tool_invoke_success():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 2, "method": "tool.invoke",
        "params": {"name": "say_hi", "args": {"name": "world"}},
    })
    resp = await _rpc_recv(proc.stdout)
    assert resp.get("result") == "hi world"
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_invoke_unknown_tool_returns_error():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {
        "jsonrpc": "2.0", "id": 3, "method": "tool.invoke",
        "params": {"name": "nope", "args": {}},
    })
    resp = await _rpc_recv(proc.stdout)
    assert "error" in resp
    assert resp["error"]["code"] == -32601
    proc.terminate()
    await proc.wait()


@pytest.mark.asyncio
async def test_shutdown_clean_exit():
    proc = await _spawn_host(FIX / "good_plugin", perms=[])
    await _rpc_send(proc.stdin, {"jsonrpc": "2.0", "id": 4, "method": "shutdown"})
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    assert proc.returncode == 0
