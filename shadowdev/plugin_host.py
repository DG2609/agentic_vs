"""Plugin host — runs inside each plugin's subprocess.

Protocol: length-prefixed JSON-RPC 2.0 over stdin/stdout.
Framing: 4-byte big-endian length, then JSON body (UTF-8).
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import struct
import subprocess as _subprocess
import sys
import traceback
from pathlib import Path


class PermissionDenied(PermissionError):
    pass


_ALLOW_NET: list[str] = []
_ALLOW_FS_READ: list[str] = []
_ALLOW_FS_WRITE: list[str] = []
_ALLOW_SUBPROCESS: bool = False
_ALLOW_ENV: bool = False


def _install_gates(perms: list[str]) -> None:
    global _ALLOW_SUBPROCESS, _ALLOW_ENV
    for p in perms:
        if p == "subprocess":
            _ALLOW_SUBPROCESS = True
        elif p == "env":
            _ALLOW_ENV = True
        elif p.startswith("net.http"):
            if "=" in p:
                _ALLOW_NET.extend(_parse_list(p.split("=", 1)[1]))
            else:
                _ALLOW_NET.append("*")
        elif p.startswith("fs.read"):
            if "=" in p:
                _ALLOW_FS_READ.extend(_resolve_roots(p.split("=", 1)[1]))
            else:
                _ALLOW_FS_READ.append(os.sep)
        elif p.startswith("fs.write"):
            if "=" in p:
                _ALLOW_FS_WRITE.extend(_resolve_roots(p.split("=", 1)[1]))
            else:
                _ALLOW_FS_WRITE.append(os.sep)

    orig_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else ""
        if "*" not in _ALLOW_NET and host.lower() not in (h.lower() for h in _ALLOW_NET):
            raise PermissionDenied(f"denied: net.http ({host})")
        return orig_connect(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]

    import builtins
    orig_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        path = os.fspath(file)
        try:
            real = Path(path).resolve()
        except OSError:
            real = Path(path)
        is_write = any(c in mode for c in ("w", "a", "x", "+"))
        roots = _ALLOW_FS_WRITE if is_write else _ALLOW_FS_READ
        allowed = any(_is_under(real, Path(r).resolve()) for r in roots) if roots else False
        if not allowed:
            raise PermissionDenied(f"denied: fs.{'write' if is_write else 'read'} ({real})")
        return orig_open(file, mode, *args, **kwargs)

    builtins.open = guarded_open  # type: ignore[assignment]

    orig_popen = _subprocess.Popen

    class GuardedPopen(orig_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            if not _ALLOW_SUBPROCESS:
                raise PermissionDenied("denied: subprocess")
            super().__init__(*args, **kwargs)

    _subprocess.Popen = GuardedPopen  # type: ignore[assignment]


def _parse_list(s: str) -> list[str]:
    s = s.strip().lstrip("[").rstrip("]")
    return [item.strip() for item in s.split(",") if item.strip()]


def _resolve_roots(s: str) -> list[str]:
    return [os.path.expanduser(r) for r in _parse_list(s)]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_plugin(plugin_dir: str) -> dict:
    pdir = Path(plugin_dir)
    manifest = json.loads((pdir / "plugin.json").read_text(encoding="utf-8"))
    entry = manifest["entry"]

    sys.path.insert(0, str(pdir))
    module = importlib.import_module(entry)
    tools = {}
    for t in getattr(module, "__skill_tools__", []):
        name = getattr(t, "name", getattr(t, "__name__", None))
        if name:
            tools[name] = t
    return tools


def _read_frame() -> dict | None:
    hdr = sys.stdin.buffer.read(4)
    if not hdr or len(hdr) < 4:
        return None
    (n,) = struct.unpack(">I", hdr)
    body = sys.stdin.buffer.read(n)
    if len(body) < n:
        return None
    return json.loads(body.decode("utf-8"))


def _write_frame(obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack(">I", len(data)) + data)
    sys.stdout.buffer.flush()


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def main() -> int:
    req = _read_frame()
    if req is None or req.get("method") != "handshake":
        _write_frame(_err(None, -32600, "expected handshake"))
        return 1
    rid = req.get("id")
    params = req.get("params") or {}
    try:
        _install_gates(params.get("permissions") or [])
        tools = _load_plugin(params["plugin_dir"])
    except Exception as e:
        _write_frame(_err(rid, -32000, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
        return 1
    _write_frame(_ok(rid, {"ok": True, "tools": [{"name": n} for n in tools]}))

    while True:
        req = _read_frame()
        if req is None:
            return 0
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        try:
            if method == "tool.list":
                _write_frame(_ok(rid, [{"name": n} for n in tools]))
            elif method == "tool.invoke":
                name = params.get("name")
                args = params.get("args") or {}
                if name not in tools:
                    _write_frame(_err(rid, -32601, f"unknown tool: {name}"))
                    continue
                try:
                    result = tools[name].invoke(args)
                except PermissionDenied as pe:
                    _write_frame(_err(rid, -32001, str(pe)))
                    continue
                _write_frame(_ok(rid, result))
            elif method == "shutdown":
                _write_frame(_ok(rid, {"ok": True}))
                return 0
            else:
                _write_frame(_err(rid, -32601, f"unknown method: {method}"))
        except Exception as e:
            _write_frame(_err(rid, -32000, f"{type(e).__name__}: {e}"))


if __name__ == "__main__":
    sys.exit(main())
