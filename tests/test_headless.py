"""Tests for agent/headless.py."""
import asyncio
import json
import sys
from unittest.mock import MagicMock, patch

from agent.headless import (
    HeadlessResult,
    _extract_reply,
    print_result,
)


# ── _extract_reply ─────────────────────────────────────────────

def test_extract_reply_dict():
    assert _extract_reply({"message": "hello"}) == "hello"


def test_extract_reply_dict_missing_key():
    assert _extract_reply({"other": "val"}) == ""


def test_extract_reply_json_string():
    assert _extract_reply('{"message": "from json"}') == "from json"


def test_extract_reply_invalid_string():
    assert _extract_reply("not json") == ""


def test_extract_reply_none():
    assert _extract_reply(None) == ""


# ── print_result ───────────────────────────────────────────────

def test_print_result_json_format(capsys):
    result = HeadlessResult(
        success=True,
        messages=["Hello!"],
        tool_calls=[{"tool": "file_read", "args": {}, "run_id": "x"}],
        errors=[],
        elapsed=1.23,
        exit_code=0,
    )
    print_result(result, "json")
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["success"] is True
    assert data["messages"] == ["Hello!"]
    assert data["elapsed_seconds"] == 1.23
    assert data["errors"] == []


def test_print_result_stream_json_format(capsys):
    result = HeadlessResult(
        success=True,
        messages=["done"],
        elapsed=0.5,
        exit_code=0,
    )
    print_result(result, "stream-json")
    out = capsys.readouterr().out
    data = json.loads(out.strip())
    assert data["type"] == "done"
    assert data["success"] is True
    assert data["exit_code"] == 0


def test_print_result_text_no_errors(capsys):
    result = HeadlessResult(success=True, messages=["hi"], exit_code=0)
    print_result(result, "text")
    out, err = capsys.readouterr()
    assert out == ""  # messages already printed during streaming
    assert err == ""


def test_print_result_text_with_errors(capsys):
    result = HeadlessResult(success=False, errors=["something broke"], exit_code=1)
    print_result(result, "text")
    _, err = capsys.readouterr()
    assert "something broke" in err


# ── HeadlessResult dataclass ───────────────────────────────────

def test_headless_result_defaults():
    r = HeadlessResult(success=True)
    assert r.messages == []
    assert r.tool_calls == []
    assert r.errors == []
    assert r.elapsed == 0.0
    assert r.exit_code == 0


def test_headless_result_failure():
    r = HeadlessResult(success=False, exit_code=1, errors=["boom"])
    assert r.success is False
    assert r.exit_code == 1


# ── run_headless: mocked graph ─────────────────────────────────

def _make_mock_graph(events):
    """Build a mock graph.astream_events that yields the given events."""
    async def _fake_stream_events(input_state, config, version):
        for event in events:
            yield event

    mock_graph = MagicMock()
    mock_graph.astream_events = _fake_stream_events
    return mock_graph


def test_run_headless_collects_reply_messages():
    """reply_to_user messages are collected into result.messages."""
    from agent import headless as hm

    events = [
        {
            "event": "on_tool_start",
            "name": "reply_to_user",
            "run_id": "r1",
            "data": {"input": {"message": "Task complete!"}},
        }
    ]
    mock_graph = _make_mock_graph(events)

    # build_graph is imported inside run_headless — patch at source module
    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless("Do something", output_format="json"))

    assert result.success is True
    assert "Task complete!" in result.messages
    assert result.exit_code == 0


def test_run_headless_records_tool_calls():
    """All tool_start events are recorded in result.tool_calls."""
    from agent import headless as hm

    events = [
        {
            "event": "on_tool_start",
            "name": "file_read",
            "run_id": "r2",
            "data": {"input": {"file_path": "/foo"}},
        }
    ]
    mock_graph = _make_mock_graph(events)

    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless("Read a file", output_format="json"))

    assert any(tc["tool"] == "file_read" for tc in result.tool_calls)


def test_run_headless_timeout():
    """Timeout produces exit_code=124 and error message."""
    from agent import headless as hm

    async def _slow_stream(input_state, config, version):
        await asyncio.sleep(10)
        yield {}  # never reached

    mock_graph = MagicMock()
    mock_graph.astream_events = _slow_stream

    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless("slow task", timeout=1, output_format="json"))

    assert result.exit_code == 124
    assert result.success is False
    assert len(result.errors) > 0


def test_run_headless_graph_error():
    """Graph exception → exit_code=1, error recorded."""
    from agent import headless as hm

    async def _bad_stream(input_state, config, version):
        raise RuntimeError("graph exploded")
        yield {}  # unreachable

    mock_graph = MagicMock()
    mock_graph.astream_events = _bad_stream

    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless("broken", output_format="json"))

    assert result.exit_code == 1
    assert "graph exploded" in result.errors[0]


def test_run_headless_allowed_tools_filter():
    """allowed_tools filters which tool_calls are recorded."""
    from agent import headless as hm

    events = [
        {
            "event": "on_tool_start",
            "name": "file_read",
            "run_id": "r1",
            "data": {"input": {}},
        },
        {
            "event": "on_tool_start",
            "name": "terminal_exec",
            "run_id": "r2",
            "data": {"input": {"command": "ls"}},
        },
    ]
    mock_graph = _make_mock_graph(events)

    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless(
            "do stuff",
            allowed_tools=["file_read"],
            output_format="json",
        ))

    tool_names = [tc["tool"] for tc in result.tool_calls]
    assert "file_read" in tool_names
    assert "terminal_exec" not in tool_names


def test_run_headless_dedup_reply_messages():
    """reply_to_user events with the same run_id are not duplicated."""
    from agent import headless as hm

    events = [
        {
            "event": "on_tool_start",
            "name": "reply_to_user",
            "run_id": "dup1",
            "data": {"input": {"message": "Only once"}},
        },
        {
            "event": "on_tool_end",
            "name": "reply_to_user",
            "run_id": "dup1",
            "data": {"input": {"message": "Only once"}, "output": ""},
        },
    ]
    mock_graph = _make_mock_graph(events)

    with patch("agent.graph.build_graph", return_value=mock_graph):
        result = asyncio.run(hm.run_headless("dedup test", output_format="json"))

    assert result.messages.count("Only once") == 1
