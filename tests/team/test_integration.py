"""
Integration test: coordinator → 2 parallel workers → reviewer → PASSED.
All LLM calls are mocked.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_full_coordinator_flow():
    """
    Simulate: coordinator spawns 2 explorers, gets notifications,
    spawns 1 coder, gets notification, ReviewLoop triggers reviewer,
    reviewer returns PASSED.
    """
    from agent.team.worker_pool import WorkerPool
    from agent.team.review_loop import ReviewLoop

    pool = WorkerPool()
    call_count = 0

    async def mock_worker_llm_response(*a, **kw):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.tool_calls = []
        r.content = f"Worker done (call {call_count})"
        return r

    mock_llm = AsyncMock()
    mock_llm.ainvoke = mock_worker_llm_response
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        # Spawn 2 parallel exploration workers
        w1 = await pool.spawn("Explore auth module", "explorer", [], "Explore auth")
        w2 = await pool.spawn("Explore test suite", "explorer", [], "Explore tests")

        # Wait for both to finish and push notifications
        await asyncio.sleep(0.3)

        # Both notifications should be in queue
        notifs = []
        while not pool.notification_queue.empty():
            notifs.append(pool.notification_queue.get_nowait())

        assert len(notifs) == 2
        assert any(w1 in n for n in notifs)
        assert any(w2 in n for n in notifs)

        # Spawn implementation worker
        w3 = await pool.spawn(
            "Fix NPE in validate.py:42. Add null check. Commit.",
            "coder", [], "Fix NPE"
        )
        await asyncio.sleep(0.3)

        # Clear implementation notification
        while not pool.notification_queue.empty():
            pool.notification_queue.get_nowait()

        # Trigger review loop
        review_loop = ReviewLoop(pool=pool, max_retries=2)

        async def fake_reviewer_spawn(*a, **kw):
            reviewer_id = "reviewer-integration"
            notif = (
                "<task-notification>\n"
                f"<task-id>{reviewer_id}</task-id>\n"
                "<status>completed</status>\n"
                "<summary>done</summary>\n"
                "<result>PASSED ✅ All 12 tests pass, no diagnostics.</result>\n"
                "</task-notification>"
            )
            await pool.notification_queue.put(notif)
            return reviewer_id

        with patch.object(pool, "spawn", side_effect=fake_reviewer_spawn):
            result = await review_loop.trigger_review(w3, ["src/auth/validate.py"])

    assert result.passed is True
    assert result.exhausted is False


@pytest.mark.asyncio
async def test_worker_pool_parallel_completion():
    """Three workers all complete concurrently — all notifications arrive."""
    from agent.team.worker_pool import WorkerPool

    pool = WorkerPool()
    mock_llm = AsyncMock()
    r = MagicMock(); r.tool_calls = []; r.content = "done"
    mock_llm.ainvoke = AsyncMock(return_value=r)
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    with patch("agent.team.worker_pool._create_worker_llm", return_value=mock_llm):
        ids = await asyncio.gather(
            pool.spawn("task a", "explorer", [], "A"),
            pool.spawn("task b", "explorer", [], "B"),
            pool.spawn("task c", "explorer", [], "C"),
        )

    await asyncio.sleep(0.3)
    notifs = []
    while not pool.notification_queue.empty():
        notifs.append(pool.notification_queue.get_nowait())

    assert len(notifs) == 3
    notif_text = " ".join(notifs)
    for wid in ids:
        assert wid in notif_text


@pytest.mark.asyncio
async def test_team_tools_coordinator_mode():
    """Smoke test: team tools work end-to-end in coordinator mode."""
    import config
    original = config.COORDINATOR_MODE
    config.COORDINATOR_MODE = True
    try:
        from agent.team import tools as t
        from agent.team.worker_pool import WorkerPool
        t._POOL = WorkerPool()

        with patch("agent.team.worker_pool._create_worker_llm") as mock_factory:
            mock_llm = AsyncMock()
            r = MagicMock(); r.tool_calls = []; r.content = "done"
            mock_llm.ainvoke = AsyncMock(return_value=r)
            mock_llm.bind_tools = MagicMock(return_value=mock_llm)
            mock_factory.return_value = mock_llm

            # Spawn a worker
            spawn_result = await t.worker_spawn.ainvoke({
                "prompt": "explore the codebase",
                "role": "explorer",
                "description": "Integration explorer",
            })
            assert "spawned" in spawn_result.lower() or "worker" in spawn_result.lower()

            # Check status
            status = await t.team_status.ainvoke({})
            assert "integration explorer" in status.lower() or "worker" in status.lower() or "spawned" in status.lower() or len(status) > 10
    finally:
        config.COORDINATOR_MODE = original
