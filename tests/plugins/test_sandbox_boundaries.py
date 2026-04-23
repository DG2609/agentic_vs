from pathlib import Path
import pytest

from agent.plugins.sandbox import RuntimeSandbox

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_network_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_net", {})
        assert "net.http" in str(ei.value).lower() or "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_network_create_connection_denied():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_net_create_connection", {})
        assert "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_network_getaddrinfo_denied():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_net_getaddrinfo", {})
        assert "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_fs_os_open_denied():
    """os.open bypasses builtins.open — make sure the lower path is also gated."""
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_fs_os_open", {})
        assert "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_fs_read_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception) as ei:
            await sb.invoke("bad_fs", {})
        assert "fs.read" in str(ei.value).lower() or "denied" in str(ei.value).lower()
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_subprocess_denied_by_default():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[])
    await sb.start()
    try:
        with pytest.raises(Exception):
            await sb.invoke("bad_sub", {})
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_timeout_kills_subprocess():
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[], call_timeout_s=2.0)
    await sb.start()
    try:
        with pytest.raises(TimeoutError):
            await sb.invoke("slow", {})
    finally:
        await sb.stop()
    assert sb._proc is None or sb._proc.returncode is not None


@pytest.mark.asyncio
async def test_mid_invoke_crash_surfaces_sandbox_error():
    """Plugin host exits before replying — sandbox must raise, not hang."""
    from agent.plugins.sandbox import SandboxError
    sb = RuntimeSandbox(plugin_dir=FIX / "hostile_plugin", permissions=[], call_timeout_s=5.0)
    await sb.start()
    try:
        with pytest.raises((SandboxError, TimeoutError)):
            await sb.invoke("suicide", {})
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_good_plugin_list_and_invoke():
    sb = RuntimeSandbox(plugin_dir=FIX / "good_plugin", permissions=[])
    await sb.start()
    try:
        tools = sb.tool_names()
        assert "say_hi" in tools
        result = await sb.invoke("say_hi", {"name": "abc"})
        assert result == "hi abc"
    finally:
        await sb.stop()
