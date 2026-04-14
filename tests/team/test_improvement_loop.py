"""
Tests for run_improvement_loop entry point and ImprovementOrchestrator.run().
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_run_improvement_loop_returns_summary(tmp_path):
    """run_improvement_loop returns a non-empty summary string."""
    from agent.team.worker_pool import WorkerPool
    from agent.team.tools import _POOL
    import agent.team.tools as team_tools

    # Fresh pool so tests don't share state
    fresh_pool = WorkerPool()
    team_tools._POOL = fresh_pool

    call_n = 0

    async def fake_spawn(prompt, role, tools, description, max_steps=30, team=None):
        nonlocal call_n
        call_n += 1
        wid = f"fake-worker-{call_n:04d}"

        if role == "explorer":
            result = (
                "TASK: Fix quality issue in sample.py\n"
                f"FILES: {tmp_path}/sample.py\n"
                "PRIORITY: 1\n---\n"
            )
        elif role == "general":  # analyst
            result = (
                "TASK: Add docstrings to sample.py\n"
                f"FILES: {tmp_path}/sample.py\n"
                "PRIORITY: 2\n---\n"
            )
        elif role == "coder":
            result = "Fixed. Committed abc123."
        elif role == "reviewer":
            result = "PASSED ✅ All checks pass."
        else:
            result = "done"

        notif = (
            f"<task-notification>"
            f"<task-id>{wid}</task-id>"
            f"<status>completed</status>"
            f"<result>{result}</result>"
            f"</task-notification>"
        )
        await fresh_pool.notification_queue.put(notif)
        return wid

    with patch.object(fresh_pool, "spawn", side_effect=fake_spawn):
        from agent.team.improvement_loop import run_improvement_loop
        summary = await run_improvement_loop(
            goal="Improve code quality",
            workspace=str(tmp_path),
            max_rounds=1,
        )

    assert isinstance(summary, str)
    assert len(summary) > 0

    # Restore
    team_tools._POOL = _POOL


@pytest.mark.asyncio
async def test_orchestrator_run_handles_empty_plan(tmp_path):
    """Orchestrator handles a round where the analyst produces no tasks."""
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace=str(tmp_path), max_rounds=1)

    call_n = 0

    async def fake_spawn(*a, **kw):
        nonlocal call_n
        call_n += 1
        wid = f"worker-{call_n:04d}"
        # All workers return empty / no structured plan
        result = "No issues found."
        notif = (
            f"<task-notification>"
            f"<task-id>{wid}</task-id>"
            f"<status>completed</status>"
            f"<result>{result}</result>"
            f"</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return wid

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        summary = await orch.run("Improve quality")

    assert "Round 1" in summary
    # 0 tasks planned — no passed/failed counted
    assert "0 tasks" in summary or "SKIPPED" in summary or "PASSED" in summary


@pytest.mark.asyncio
async def test_orchestrator_run_continues_on_exception(tmp_path):
    """A round-level exception is caught and reported; subsequent rounds proceed."""
    from agent.team.worker_pool import WorkerPool
    from agent.team.orchestrator import ImprovementOrchestrator

    pool = WorkerPool()
    orch = ImprovementOrchestrator(pool, workspace=str(tmp_path), max_rounds=2)

    round_n = 0

    async def fake_spawn(*a, **kw):
        nonlocal round_n
        round_n += 1
        if round_n == 1:
            raise RuntimeError("Simulated analysis failure")
        wid = f"worker-ok-{round_n}"
        notif = (
            f"<task-notification><task-id>{wid}</task-id>"
            f"<status>completed</status><result>done</result>"
            f"</task-notification>"
        )
        await pool.notification_queue.put(notif)
        return wid

    with patch.object(pool, "spawn", side_effect=fake_spawn):
        summary = await orch.run("Improve code")

    # Round 1 errored, Round 2 should also run (may produce 0 tasks)
    assert "Round 1" in summary
    assert "ERROR" in summary or "Round 2" in summary
