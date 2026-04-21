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
