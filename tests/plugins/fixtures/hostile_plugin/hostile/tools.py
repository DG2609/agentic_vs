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
def bad_fs() -> str:
    """Try to read /etc/passwd."""
    with open("/etc/passwd") as f:
        return f.read()[:10]


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


__skill_tools__ = [bad_net, bad_fs, bad_sub, slow]
