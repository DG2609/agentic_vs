"""Tests for the async worker pool."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def pool():
    from agent.team.worker_pool import WorkerPool
    p = WorkerPool()
    yield p
    for entry in p._workers.values():
        if not entry.task.done():
            entry.task.cancel()


def test_pool_starts_empty(pool):
    assert pool.list_workers() == []


@pytest.mark.asyncio
async def test_spawn_returns_uuid(pool):
    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.tool_calls = []
    mock_response.content = "done"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        worker_id = await pool.spawn(
            prompt="explore auth module",
            role="explorer",
            tools=[],
            description="Test worker",
        )
    assert len(worker_id) == 36


@pytest.mark.asyncio
async def test_spawn_worker_appears_in_list(pool):
    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.tool_calls = []
    mock_response.content = "done"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        worker_id = await pool.spawn(
            prompt="test task",
            role="general",
            tools=[],
            description="My worker",
        )
    workers = pool.list_workers()
    assert any(w["id"] == worker_id for w in workers)


@pytest.mark.asyncio
async def test_stop_worker(pool):
    mock_llm = AsyncMock()
    async def slow_invoke(*a, **kw):
        await asyncio.sleep(10)
    mock_llm.ainvoke = slow_invoke
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        worker_id = await pool.spawn(
            prompt="slow task", role="general", tools=[], description="Slow worker",
        )
    await asyncio.sleep(0.05)
    result = pool.stop(worker_id)
    assert "stopped" in result.lower()


@pytest.mark.asyncio
async def test_stop_unknown_worker(pool):
    result = pool.stop("nonexistent-id")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_notification_pushed_on_completion(pool):
    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.tool_calls = []
    mock_response.content = "task complete"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        worker_id = await pool.spawn(
            prompt="quick task", role="general", tools=[], description="Quick worker",
        )
    await asyncio.sleep(0.3)
    assert not pool.notification_queue.empty()
    notif = pool.notification_queue.get_nowait()
    assert worker_id in notif
    assert "completed" in notif


@pytest.mark.asyncio
async def test_send_message_to_worker(pool):
    mock_llm = AsyncMock()
    call_count = 0

    async def respond(*a, **kw):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.tool_calls = []
        if call_count == 1:
            r.content = ""
            await asyncio.sleep(0.05)
        else:
            r.content = "done after message"
        return r

    mock_llm.ainvoke = respond
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        worker_id = await pool.spawn(
            prompt="waiting task", role="general", tools=[], description="Waiting worker", max_steps=5,
        )
    result = pool.send_message(worker_id, "continue now")
    assert "queued" in result.lower() or "sent" in result.lower()
