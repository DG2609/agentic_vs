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

    call_count = 0
    impl_messages = []

    async def fake_spawn(*a, **kw):
        nonlocal call_count
        call_count += 1
        reviewer_id = f"reviewer-{call_count}"
        status_word = "FAILED ❌" if call_count < 2 else "PASSED ✅"
        notif = (
            "<task-notification>\n"
            f"<task-id>{reviewer_id}</task-id>\n"
            "<status>completed</status>\n"
            "<summary>done</summary>\n"
            f"<result>{status_word} some issues.</result>\n"
            "</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return reviewer_id

    def fake_send_message(worker_id, msg):
        impl_messages.append(msg)
        return "queued"

    loop = ReviewLoop(pool=pool, max_retries=3)
    with patch.object(pool, "spawn", side_effect=fake_spawn):
        with patch.object(pool, "send_message", side_effect=fake_send_message):
            result = await loop.trigger_review(
                impl_worker_id="impl-abc",
                changed_files=["src/auth/validate.py"],
            )
    assert result.passed is True
    assert len(impl_messages) == 1


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
        with patch.object(pool, "send_message", return_value="queued"):
            result = await loop.trigger_review(
                impl_worker_id="impl-abc",
                changed_files=["src/auth/validate.py"],
            )
    assert result.passed is False
    assert result.exhausted is True
    assert "FAILED" in result.issues
