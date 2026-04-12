"""
Tests for the HTTP SSE /stream endpoint and /health endpoint.

Uses aiohttp.test_utils (built into aiohttp) — no pytest-aiohttp needed.
"""
import json
import sys
import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _collect_sse(response) -> list[dict]:
    """Read all SSE data lines from an aiohttp response and parse JSON."""
    events = []
    async for line in response.content:
        decoded = line.decode().strip()
        if decoded.startswith("data: "):
            try:
                events.append(json.loads(decoded[len("data: "):]))
            except json.JSONDecodeError:
                pass
    return events


# ── Mock graph ────────────────────────────────────────────────────────────────

def make_mock_graph():
    """Return a minimal mock LangGraph that yields token, tool events."""
    async def _astream_events(input_state, config=None, version="v2"):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": MagicMock(content="Hello")},
        }
        yield {
            "event": "on_tool_start",
            "name": "file_read",
            "data": {"input": {"file_path": "src/foo.py"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "file_read",
            "data": {"output": MagicMock(content="# content")},
        }

    g = MagicMock()
    g.astream_events = _astream_events
    return g


# ── App factory (for tests) ────────────────────────────────────────────────────

async def _build_app():
    """Build the aiohttp app with all heavy I/O mocked out."""
    import server.main as server_mod
    mock_graph = make_mock_graph()
    server_mod.graph = mock_graph

    with patch("server.main.build_graph", return_value=mock_graph), \
         patch("server.main.AsyncSqliteSaver") as mock_saver_cls:
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_saver_cls.from_conn_string.return_value = mock_cm
        application = await server_mod.create_app()

    return application


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health should return 200 with status='ok', tools (int), model (str)."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "tools" in data
        assert "model" in data
        assert isinstance(data["tools"], int)
        assert isinstance(data["model"], str)


@pytest.mark.asyncio
async def test_stream_endpoint_returns_sse():
    """POST /stream should respond with Content-Type: text/event-stream."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={"prompt": "say hello"})
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/event-stream" in content_type


@pytest.mark.asyncio
async def test_stream_endpoint_emits_done_event():
    """POST /stream should emit a 'done' event with matching thread_id."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/stream",
            json={"prompt": "say hello", "thread_id": "test-thread-42"},
        )
        assert resp.status == 200

        events = await _collect_sse(resp)
        assert events, "Expected at least one SSE event"

        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1, f"Expected exactly one 'done' event, got: {done_events}"
        assert done_events[0]["thread_id"] == "test-thread-42"


@pytest.mark.asyncio
async def test_stream_endpoint_emits_token_events():
    """POST /stream should relay 'token' events from the graph."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={"prompt": "say hello"})
        assert resp.status == 200

        events = await _collect_sse(resp)
        token_events = [e for e in events if e.get("type") == "token"]
        assert token_events, "Expected at least one token event"
        assert all("content" in e for e in token_events)


@pytest.mark.asyncio
async def test_stream_endpoint_emits_tool_events():
    """POST /stream should emit 'tool_start' and 'tool_end' events."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={"prompt": "read a file"})
        assert resp.status == 200

        events = await _collect_sse(resp)
        tool_start = [e for e in events if e.get("type") == "tool_start"]
        tool_end = [e for e in events if e.get("type") == "tool_end"]
        assert tool_start, "Expected at least one tool_start event"
        assert tool_end, "Expected at least one tool_end event"
        assert tool_start[0]["name"] == "file_read"


@pytest.mark.asyncio
async def test_stream_endpoint_missing_prompt():
    """POST /stream without 'prompt' should return HTTP 400."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_stream_endpoint_cors_headers():
    """POST /stream response should include CORS and no-cache SSE headers."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={"prompt": "hi"})
        assert resp.status == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"


@pytest.mark.asyncio
async def test_stream_endpoint_auto_assigns_thread_id():
    """POST /stream without thread_id should auto-assign a UUID in the done event."""
    app = await _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/stream", json={"prompt": "hi"})
        assert resp.status == 200

        events = await _collect_sse(resp)
        done_events = [e for e in events if e.get("type") == "done"]
        assert done_events
        assert done_events[0].get("thread_id"), "Expected non-empty auto-assigned thread_id"
