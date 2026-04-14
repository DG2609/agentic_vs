"""
Tests for ImprovementOrchestrator and _parse_task_list.
All LLM calls are mocked — no real network traffic.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────
# _parse_task_list
# ─────────────────────────────────────────────────────────────

def test_parse_task_list_happy_path():
    from agent.team.orchestrator import _parse_task_list

    text = (
        "TASK: Fix null check in validate.py\n"
        "FILES: src/auth/validate.py, tests/test_validate.py\n"
        "PRIORITY: 1\n"
        "---\n"
        "TASK: Add tests for calculator.py\n"
        "FILES: src/calculator.py\n"
        "PRIORITY: 2\n"
        "---\n"
    )
    tasks = _parse_task_list(text)
    assert len(tasks) == 2
    assert tasks[0].priority == 1
    assert tasks[0].goal == "Fix null check in validate.py"
    assert "src/auth/validate.py" in tasks[0].files
    assert tasks[1].goal == "Add tests for calculator.py"


def test_parse_task_list_sorted_by_priority():
    from agent.team.orchestrator import _parse_task_list

    text = (
        "TASK: Low priority task\nFILES: a.py\nPRIORITY: 4\n---\n"
        "TASK: High priority task\nFILES: b.py\nPRIORITY: 1\n---\n"
        "TASK: Medium priority\nFILES: c.py\nPRIORITY: 2\n---\n"
    )
    tasks = _parse_task_list(text)
    priorities = [t.priority for t in tasks]
    assert priorities == sorted(priorities)


def test_parse_task_list_max_5():
    from agent.team.orchestrator import _parse_task_list

    blocks = "\n---\n".join(
        f"TASK: Task {i}\nFILES: file{i}.py\nPRIORITY: {i % 5 + 1}"
        for i in range(8)
    )
    tasks = _parse_task_list(blocks)
    assert len(tasks) <= 5


def test_parse_task_list_unknown_files_excluded():
    from agent.team.orchestrator import _parse_task_list

    text = "TASK: Some task\nFILES: unknown\nPRIORITY: 3\n---\n"
    tasks = _parse_task_list(text)
    assert len(tasks) == 1
    assert tasks[0].files == []


def test_parse_task_list_empty_text():
    from agent.team.orchestrator import _parse_task_list
    assert _parse_task_list("") == []
    assert _parse_task_list("---\n---\n") == []


def test_parse_task_list_bad_priority_defaults():
    from agent.team.orchestrator import _parse_task_list

    text = "TASK: Fix something\nFILES: x.py\nPRIORITY: not_a_number\n---\n"
    tasks = _parse_task_list(text)
    assert len(tasks) == 1
    assert tasks[0].priority == 3  # default


# ─────────────────────────────────────────────────────────────
# ImprovementOrchestrator._collect_all
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collect_all_returns_results():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator
    import config

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws")

    # Manually push a notification for worker-abc
    wid = "worker-abc-1234-5678-abcd"
    notif = (
        f"<task-notification>\n"
        f"<task-id>{wid}</task-id>\n"
        f"<status>completed</status>\n"
        f"<summary>done</summary>\n"
        f"<result>Analysis complete — found 3 issues</result>\n"
        f"</task-notification>"
    )
    await pool.notification_queue.put(notif)

    results = await orch._collect_all([wid], timeout=5)
    assert wid in results
    assert "Analysis complete" in results[wid]


@pytest.mark.asyncio
async def test_collect_all_timeout_for_missing_worker():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws")

    results = await orch._collect_all(["ghost-worker-id"], timeout=1.5)
    assert "ghost-worker-id" in results
    assert "timeout" in results["ghost-worker-id"]


@pytest.mark.asyncio
async def test_collect_all_returns_unmatched_to_queue():
    """Notifications for other workers are put back in the queue."""
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws")

    other_id = "other-worker-9999"
    target_id = "target-worker-1234"

    # Push notification for other worker first, then target
    other_notif = (
        f"<task-notification><task-id>{other_id}</task-id>"
        f"<status>completed</status><result>other done</result></task-notification>"
    )
    target_notif = (
        f"<task-notification><task-id>{target_id}</task-id>"
        f"<status>completed</status><result>target done</result></task-notification>"
    )
    await pool.notification_queue.put(other_notif)
    await pool.notification_queue.put(target_notif)

    results = await orch._collect_all([target_id], timeout=5)
    assert target_id in results
    assert "target done" in results[target_id]

    # other_notif should be back in the queue
    assert not pool.notification_queue.empty()
    returned = pool.notification_queue.get_nowait()
    assert other_id in returned


# ─────────────────────────────────────────────────────────────
# ImprovementOrchestrator._run_reviewer
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_reviewer_passed():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws")

    reviewer_id = "reviewer-pass-1111"

    async def fake_spawn(*a, **kw):
        notif = (
            f"<task-notification>"
            f"<task-id>{reviewer_id}</task-id>"
            f"<status>completed</status>"
            f"<result>PASSED ✅ All 8 tests pass, no diagnostics.</result>"
            f"</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return reviewer_id

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        result = await orch._run_reviewer(["src/foo.py"], "Implementation done.")

    assert result.passed is True
    assert result.issues == ""


@pytest.mark.asyncio
async def test_run_reviewer_failed():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws")

    reviewer_id = "reviewer-fail-2222"

    async def fake_spawn(*a, **kw):
        notif = (
            f"<task-notification>"
            f"<task-id>{reviewer_id}</task-id>"
            f"<status>completed</status>"
            f"<result>FAILED ❌ lsp_diagnostics found 2 errors in src/foo.py:10</result>"
            f"</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return reviewer_id

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        result = await orch._run_reviewer(["src/foo.py"], "Implementation done.")

    assert result.passed is False
    assert "FAILED" in result.issues


# ─────────────────────────────────────────────────────────────
# ImprovementOrchestrator._implement_and_review full cycle
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_implement_and_review_passes_first_attempt():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator, ImprovementRound, ImprovementTask

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws", max_retries=1)

    coder_id = "coder-xxxx"
    reviewer_id = "reviewer-yyyy"
    spawn_call = 0

    async def fake_spawn(*a, **kw):
        nonlocal spawn_call
        spawn_call += 1
        if spawn_call == 1:
            # Coder notification
            notif = (
                f"<task-notification><task-id>{coder_id}</task-id>"
                f"<status>completed</status><result>Fixed. commit abc123</result>"
                f"</task-notification>"
            )
            await pool.notification_queue.put(notif)
            return coder_id
        else:
            # Reviewer notification
            notif = (
                f"<task-notification><task-id>{reviewer_id}</task-id>"
                f"<status>completed</status><result>PASSED ✅ All tests pass.</result>"
                f"</task-notification>"
            )
            await pool.notification_queue.put(notif)
            return reviewer_id

    rnd = ImprovementRound(round_num=1)
    task = ImprovementTask(goal="Fix null check", files=["src/foo.py"])

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        await orch._implement_and_review(task, rnd)

    assert rnd.passed == 1
    assert rnd.failed == 0
    assert task.review is not None
    assert task.review.passed is True


@pytest.mark.asyncio
async def test_implement_and_review_retries_on_failure():
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator, ImprovementRound, ImprovementTask

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace="/tmp/test_ws", max_retries=2)

    call_n = 0
    wid_map = {
        1: "coder-attempt1",
        2: "reviewer-attempt1",
        3: "coder-attempt2",
        4: "reviewer-attempt2-pass",
    }

    async def fake_spawn(*a, **kw):
        nonlocal call_n
        call_n += 1
        wid = wid_map.get(call_n, f"worker-{call_n}")
        if "reviewer" in wid and "pass" in wid:
            verdict = "PASSED ✅ All clean."
        elif "reviewer" in wid:
            verdict = "FAILED ❌ Missing error handling at line 5."
        else:
            verdict = "Fixed stuff."
        notif = (
            f"<task-notification><task-id>{wid}</task-id>"
            f"<status>completed</status><result>{verdict}</result>"
            f"</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return wid

    rnd = ImprovementRound(round_num=1)
    task = ImprovementTask(goal="Fix error handling", files=["src/bar.py"])

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        await orch._implement_and_review(task, rnd)

    assert rnd.passed == 1
    assert rnd.failed == 0
    assert call_n == 4  # coder1, reviewer1(fail), coder2, reviewer2(pass)
