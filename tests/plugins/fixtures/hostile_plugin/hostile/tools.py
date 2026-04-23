import os
import time
import socket
import subprocess
from langchain_core.tools import tool


@tool
def bad_net() -> str:
    """Try to open a socket."""
    s = socket.socket()
    s.connect(("example.com", 80))
    return "connected"


@tool
def bad_net_create_connection() -> str:
    """Try to open a socket via create_connection (alt path)."""
    socket.create_connection(("example.com", 80), timeout=2)
    return "connected"


@tool
def bad_net_getaddrinfo() -> str:
    """Try to resolve a name (DNS exfil path)."""
    socket.getaddrinfo("example.com", 80)
    return "resolved"


@tool
def bad_fs() -> str:
    """Try to read /etc/passwd."""
    with open("/etc/passwd") as f:
        return f.read()[:10]


@tool
def bad_fs_os_open() -> str:
    """Try to read via os.open (bypasses builtins.open)."""
    fd = os.open("/etc/passwd", os.O_RDONLY)
    try:
        return os.read(fd, 10).decode("utf-8", "replace")
    finally:
        os.close(fd)


@tool
def bad_sub() -> str:
    """Try to run echo."""
    subprocess.run(["echo", "hi"], check=True)
    return "ran"


@tool
def slow() -> str:
    """Sleep 60s."""
    time.sleep(60)
    return "done"


__skill_tools__ = [
    bad_net, bad_net_create_connection, bad_net_getaddrinfo,
    bad_fs, bad_fs_os_open,
    bad_sub, slow,
]
