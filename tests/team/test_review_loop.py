"""Tests for auto review-and-retry loop."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def pool_with_mock():
    from agent.team.worker_pool import WorkerPool
    pool = WorkerPool()
    return pool


@pytest.mark.asyncio
async def test_review_loop_passed(pool_with_mock):
    from agent.team.review_loop import ReviewLoop
    pool = pool_with_mock

    async def fake_spawn(*a, **kw):
        reviewer_id = "reviewer-fake-uuid"
        notif = (
            "<task-notification>\n"
            f"<task-id>{reviewer_id}</task-id>\n"
            "<status>completed</status>\n"
            "<summary>done</summary>\n"
            "<result>PASSED ✅ All tests pass, no diagnostics.</result>\n"
            "</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return reviewer_id

    loop = ReviewLoop(pool=pool, max_retries=3)
    with patch.object(pool, "spawn", side_effect=fake_spawn):
        result = await loop.trigger_review(
            impl_worker_id="impl-abc",
            changed_files=["src/auth/validate.py"],
        )
    assert result.passed is True
    assert result.exhausted is False


@pytest.mark.asyncio
async def test_review_loop_failed_then_retried(pool_with_mock):
    from agent.team.review_loop import ReviewLoop
    pool = pool_with_mock

    spawn_roles = []
    call_count = 0

    async def fake_spawn(*a, role="general", **kw):
        nonlocal call_count
        call_count += 1
        spawn_roles.append(role)
        worker_id = f"worker-{call_count}"
        # Only reviewer spawns get a verdict notification; coder spawns don't
        if role == "reviewer":
            status_word = "FAILED ❌" if call_count == 1 else "PASSED ✅"
            notif = (
                "<task-notification>\n"
                f"<task-id>{worker_id}</task-id>\n"
                "<status>completed</status>\n"
                "<summary>done</summary>\n"
                f"<result>{status_word} some issues.</result>\n"
                "</task-notification>"
            )
            await pool.notification_queue.put(notif)
        return worker_id

    loop = ReviewLoop(pool=pool, max_retries=3)
    with patch.object(pool, "spawn", side_effect=fake_spawn):
        result = await loop.trigger_review(
            impl_worker_id="impl-abc",
            changed_files=["src/auth/validate.py"],
        )
    assert result.passed is True
    # On FAILED review: a fresh coder is spawned, then a new reviewer is spawned
    assert spawn_roles.count("reviewer") == 2
    assert spawn_roles.count("coder") == 1


@pytest.mark.asyncio
async def test_review_loop_exhausted(pool_with_mock):
    from agent.team.review_loop import ReviewLoop
    pool = pool_with_mock

    async def always_fail(*a, **kw):
        reviewer_id = "reviewer-fail"
        notif = (
            "<task-notification>\n"
            f"<task-id>{reviewer_id}</task-id>\n"
            "<status>completed</status>\n"
            "<summary>done</summary>\n"
            "<result>FAILED ❌ Critical error remains.</result>\n"
            "</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return reviewer_id

    loop = ReviewLoop(pool=pool, max_retries=2)
    with patch.object(pool, "spawn", side_effect=always_fail):
        result = await loop.trigger_review(
            impl_worker_id="impl-abc",
            changed_files=["src/auth/validate.py"],
        )
    assert result.passed is False
    assert result.exhausted is True
    assert "FAILED" in result.issues
