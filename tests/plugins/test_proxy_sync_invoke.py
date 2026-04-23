"""Tests for _ProxyTool sync/async invoke safety across event-loop contexts."""
import asyncio
import pytest

from agent.plugins.manager import _run_sync, set_background_loop


def test_sync_invoke_outside_loop_runs_coro():
    async def coro():
        return 42
    assert _run_sync(coro()) == 42


def test_sync_invoke_inside_running_loop_raises_without_bg():
    async def outer():
        async def inner():
            return 1
        coro = inner()
        try:
            with pytest.raises(RuntimeError, match="running event loop"):
                _run_sync(coro)
        finally:
            coro.close()
    asyncio.run(outer())


def test_sync_invoke_uses_background_loop_if_registered():
    import threading

    result_holder = {}

    def run_bg_loop(loop: asyncio.AbstractEventLoop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    bg = asyncio.new_event_loop()
    t = threading.Thread(target=run_bg_loop, args=(bg,), daemon=True)
    t.start()
    set_background_loop(bg)
    try:
        async def outer():
            async def inner():
                return "ok"
            result_holder["v"] = _run_sync(inner())

        asyncio.run(outer())
        assert result_holder["v"] == "ok"
    finally:
        set_background_loop(None)
        bg.call_soon_threadsafe(bg.stop)
        t.join(timeout=2)
        bg.close()
