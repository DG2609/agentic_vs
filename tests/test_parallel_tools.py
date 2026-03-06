"""Tests for HookedToolNode parallel execution (3B-2)."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from agent.hooks import (
    HookResult, PRE_TOOL_HOOKS, POST_TOOL_HOOKS,
    register_hook, clear_hooks,
)


# ── Helpers ───────────────────────────────────────────────────

def make_tool(name: str, return_value: str = "ok") -> MagicMock:
    """Create a minimal mock LangChain tool."""
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=return_value)
    return tool


def make_state(tool_calls: list) -> dict:
    """Create a fake graph state with an AIMessage containing tool_calls."""
    msg = AIMessage(content="", tool_calls=tool_calls)
    return {"messages": [msg]}


def make_tc(name: str, args: dict, tc_id: str) -> dict:
    return {"name": name, "args": args, "id": tc_id}


def _make_node(tools):
    """Create HookedToolNode with ToolNode patched out (mocks aren't real tools)."""
    from agent.graph import HookedToolNode
    with patch("agent.graph.ToolNode") as mock_tn_class:
        mock_inner = MagicMock()
        mock_inner.ainvoke = AsyncMock(return_value={"messages": []})
        mock_tn_class.return_value = mock_inner
        node = HookedToolNode(tools)
    return node


@pytest.fixture(autouse=True)
def _clear_hooks():
    clear_hooks()
    yield
    clear_hooks()


# ── Test: no hooks → delegates to inner ToolNode ─────────────

def test_no_hooks_delegates_to_inner():
    """When no hooks registered, __call__ uses self._inner.ainvoke."""
    tool = make_tool("file_read", "content")
    node = _make_node([tool])
    assert not node._has_hooks

    inner_result = {"messages": [ToolMessage(content="content", tool_call_id="id1", name="file_read")]}
    node._inner.ainvoke = AsyncMock(return_value=inner_result)

    state = make_state([make_tc("file_read", {"path": "/tmp/x"}, "id1")])
    result = asyncio.run(node(state))

    node._inner.ainvoke.assert_awaited_once_with(state)
    assert result == inner_result


def test_no_tool_calls_delegates_to_inner():
    """When last message has no tool_calls, fallback to inner."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    tool = make_tool("file_read")
    node = _make_node([tool])
    assert node._has_hooks

    node._inner.ainvoke = AsyncMock(return_value={"messages": []})
    state = {"messages": [AIMessage(content="hello")]}
    asyncio.run(node(state))

    node._inner.ainvoke.assert_awaited_once()


# ── Test: parallel execution ─────────────────────────────────

def test_all_tools_are_called():
    """All tools in tool_calls batch get invoked."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    t1 = make_tool("tool_a", "result_a")
    t2 = make_tool("tool_b", "result_b")
    t3 = make_tool("tool_c", "result_c")
    node = _make_node([t1, t2, t3])

    state = make_state([
        make_tc("tool_a", {}, "id1"),
        make_tc("tool_b", {}, "id2"),
        make_tc("tool_c", {}, "id3"),
    ])
    result = asyncio.run(node(state))

    msgs = result["messages"]
    assert len(msgs) == 3
    t1.ainvoke.assert_awaited_once()
    t2.ainvoke.assert_awaited_once()
    t3.ainvoke.assert_awaited_once()


def test_tools_run_concurrently():
    """Tools execute in parallel: total time ≈ max(individual), not sum."""
    import time
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())

    async def slow(args):
        await asyncio.sleep(0.05)
        return "done"

    t1 = make_tool("slow_a"); t1.ainvoke = slow
    t2 = make_tool("slow_b"); t2.ainvoke = slow
    node = _make_node([t1, t2])

    state = make_state([
        make_tc("slow_a", {}, "id1"),
        make_tc("slow_b", {}, "id2"),
    ])

    start = time.perf_counter()
    result = asyncio.run(node(state))
    elapsed = time.perf_counter() - start

    assert len(result["messages"]) == 2
    # Sequential would take ~0.10s; parallel takes ~0.05s
    assert elapsed < 0.09, f"Tools appear to be running sequentially (elapsed={elapsed:.3f}s)"


def test_ordering_preserved():
    """ToolMessages appear in the same order as tool_calls, regardless of execution speed."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())

    async def make_and_run():
        async def slow_first(args):
            await asyncio.sleep(0.05)
            return "first"

        async def fast_second(args):
            await asyncio.sleep(0.01)
            return "second"

        async def mid_third(args):
            await asyncio.sleep(0.03)
            return "third"

        t1 = make_tool("t1"); t1.ainvoke = slow_first
        t2 = make_tool("t2"); t2.ainvoke = fast_second
        t3 = make_tool("t3"); t3.ainvoke = mid_third
        node = _make_node([t1, t2, t3])

        state = make_state([
            make_tc("t1", {}, "id1"),
            make_tc("t2", {}, "id2"),
            make_tc("t3", {}, "id3"),
        ])
        return await node(state)

    result = asyncio.run(make_and_run())
    msgs = result["messages"]
    assert [m.content for m in msgs] == ["first", "second", "third"]
    assert [m.tool_call_id for m in msgs] == ["id1", "id2", "id3"]


# ── Test: pre-hook blocking ───────────────────────────────────

def test_pre_hook_blocks_one_tool_others_still_run():
    """A blocked tool returns an error message; other tools still execute."""
    def block_if_write(name, args):
        if name == "file_write":
            return HookResult(block=True, reason="write denied")
        return HookResult()

    register_hook("pre_tool_use", pattern="*", handler=block_if_write)
    t_read = make_tool("file_read", "content")
    t_write = make_tool("file_write", "written")
    node = _make_node([t_read, t_write])

    state = make_state([
        make_tc("file_read", {"path": "/tmp/x"}, "id1"),
        make_tc("file_write", {"path": "/tmp/y", "content": "hi"}, "id2"),
    ])
    result = asyncio.run(node(state))

    msgs = result["messages"]
    assert len(msgs) == 2
    assert msgs[0].content == "content"
    assert "[BLOCKED" in msgs[1].content
    assert "write denied" in msgs[1].content
    t_write.ainvoke.assert_not_awaited()


def test_pre_hook_blocks_all_tools():
    """When all tools are blocked, all return blocked messages."""
    register_hook("pre_tool_use", pattern="*",
                  handler=lambda n, a: HookResult(block=True, reason="all blocked"))
    t1 = make_tool("tool_a")
    t2 = make_tool("tool_b")
    node = _make_node([t1, t2])

    state = make_state([
        make_tc("tool_a", {}, "id1"),
        make_tc("tool_b", {}, "id2"),
    ])
    result = asyncio.run(node(state))

    msgs = result["messages"]
    assert all("[BLOCKED" in m.content for m in msgs)
    t1.ainvoke.assert_not_awaited()
    t2.ainvoke.assert_not_awaited()


def test_pre_hook_modifies_args():
    """Pre-hook modified_args are passed to the tool instead of original."""
    def inject_arg(name, args):
        return HookResult(modified_args={"path": "/modified"})

    register_hook("pre_tool_use", pattern="file_read", handler=inject_arg)
    tool = make_tool("file_read", "ok")
    node = _make_node([tool])

    state = make_state([make_tc("file_read", {"path": "/original"}, "id1")])
    asyncio.run(node(state))

    tool.ainvoke.assert_awaited_once_with({"path": "/modified"})


# ── Test: post-hook output modification ──────────────────────

def test_post_hook_modifies_output():
    """Post-hook can replace the tool output."""
    def uppercase_output(name, args, output):
        return HookResult(modified_output=output.upper())

    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    register_hook("post_tool_use", pattern="*", handler=uppercase_output)
    tool = make_tool("tool_a", "hello world")
    node = _make_node([tool])

    state = make_state([make_tc("tool_a", {}, "id1")])
    result = asyncio.run(node(state))

    assert result["messages"][0].content == "HELLO WORLD"


def test_post_hook_no_modification_preserves_output():
    """Post-hook that returns no modification keeps original output."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    register_hook("post_tool_use", pattern="*", handler=lambda n, a, o: HookResult())
    tool = make_tool("tool_a", "original")
    node = _make_node([tool])

    state = make_state([make_tc("tool_a", {}, "id1")])
    result = asyncio.run(node(state))

    assert result["messages"][0].content == "original"


# ── Test: unknown tool ────────────────────────────────────────

def test_unknown_tool_returns_error_message():
    """If a tool name is not in the tool_map, an error message is returned."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    tool = make_tool("known_tool", "ok")
    node = _make_node([tool])

    state = make_state([
        make_tc("known_tool", {}, "id1"),
        make_tc("unknown_xyz", {}, "id2"),
    ])
    result = asyncio.run(node(state))

    msgs = result["messages"]
    assert len(msgs) == 2
    assert msgs[0].content == "ok"
    assert "Unknown tool" in msgs[1].content
    assert "unknown_xyz" in msgs[1].content


# ── Test: _invoke_one result coercion ─────────────────────────

def test_invoke_one_dict_result_json_serialized():
    """_invoke_one converts dict result to JSON string."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    tool = make_tool("tool_a")
    tool.ainvoke = AsyncMock(return_value={"key": "value", "count": 3})
    node = _make_node([tool])

    state = make_state([make_tc("tool_a", {}, "id1")])
    result = asyncio.run(node(state))

    content = result["messages"][0].content
    parsed = json.loads(content)
    assert parsed == {"key": "value", "count": 3}


def test_invoke_one_exception_returns_error_message():
    """If tool raises an exception, error message is returned (no crash)."""
    register_hook("pre_tool_use", pattern="*", handler=lambda n, a: HookResult())
    tool = make_tool("tool_a")
    tool.ainvoke = AsyncMock(side_effect=RuntimeError("tool exploded"))
    node = _make_node([tool])

    state = make_state([make_tc("tool_a", {}, "id1")])
    result = asyncio.run(node(state))

    content = result["messages"][0].content
    assert "Error" in content
    assert "tool exploded" in content
