"""
Convenience entry point for the autonomous improvement loop.

Used by the `--improve` CLI flag and direct programmatic invocations.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_improvement_loop(
    goal: str,
    workspace: str,
    max_rounds: int = 5,
    max_retries: int | None = None,
) -> str:
    """
    Start an autonomous multi-round improvement loop for *workspace*.

    Phases per round:
      1. Parallel analysis (architecture, quality, tests)
      2. Analyst synthesizes findings into a task list
      3. Coder workers implement each task (sequential)
      4. Reviewer verifies each implementation
      5. Repeat up to *max_rounds* or until all pass

    Returns a multi-line summary string.
    """
    from agent.team.tools import _POOL
    from agent.team.orchestrator import ImprovementOrchestrator

    orch = ImprovementOrchestrator(
        pool=_POOL,
        workspace=workspace,
        max_rounds=max_rounds,
        max_retries=max_retries,
    )
    return await orch.run(goal)
