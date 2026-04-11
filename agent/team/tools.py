"""
Six LangChain tools for the coordinator to manage workers.

These are added to PLANNER_TOOLS when coordinator mode is active.
A single global WorkerPool is shared across all tool calls in a session.
"""
import logging
from langchain_core.tools import tool

from agent.team.worker_pool import WorkerPool

logger = logging.getLogger(__name__)

# ── Global pool (one per session) ───────────────────────────
_POOL: WorkerPool = WorkerPool()


def _tools_for_role(role: str) -> list:
    """Return the appropriate tool set for a worker role."""
    from agent.subagents import _get_explore_tools, _get_general_tools, _get_reviewer_tools
    if role == "explorer":
        return _get_explore_tools()
    if role == "reviewer":
        return _get_reviewer_tools()
    return _get_general_tools()


@tool
async def worker_spawn(
    prompt: str,
    role: str,
    description: str,
    max_steps: int = 30,
    team: str = "",
) -> str:
    """Spawn a new async worker agent.

    The worker runs its own LLM loop with the given role's tool set.
    When done, it pushes a <task-notification> into the coordinator's stream.

    Args:
        prompt: Full self-contained task for the worker. Include file paths,
                line numbers, and what "done" looks like. Workers cannot see
                your conversation.
        role: Worker specialisation — one of: 'explorer', 'coder', 'reviewer', 'general'.
              explorer=read-only search; coder=edit+commit; reviewer=lint+test; general=all.
        description: Short human-readable label shown in team_status (e.g. "Explore auth bug").
        max_steps: Max LLM iterations before worker auto-stops (default 30).
        team: Optional team group name to tag this worker (default: none).

    Returns:
        Worker UUID to use with worker_message/worker_stop.
    """
    tools = _tools_for_role(role)
    worker_id = await _POOL.spawn(
        prompt=prompt,
        role=role,
        tools=tools,
        description=description,
        max_steps=max_steps,
        team=team or None,
    )
    return f"Worker spawned — ID: {worker_id} | Role: {role} | \"{description}\""


@tool
async def worker_message(worker_id: str, message: str) -> str:
    """Send a follow-up message to a running worker.

    Use this to continue a worker after it reports back, send corrections,
    or direct the next phase of its work. The worker picks up the message
    on its next LLM step.

    Args:
        worker_id: UUID returned by worker_spawn (from <task-id> in notification).
        message: Self-contained instruction. Worker has its prior context but
                 not your coordinator conversation — be explicit.

    Returns:
        Confirmation or error.
    """
    return _POOL.send_message(worker_id, message)


@tool
async def worker_stop(worker_id: str) -> str:
    """Cancel a running worker.

    Use when you realise the worker was sent in the wrong direction,
    or the user changes requirements mid-flight.

    Args:
        worker_id: UUID of the worker to stop.

    Returns:
        Confirmation or error.
    """
    return _POOL.stop(worker_id)


@tool
async def team_status() -> str:
    """Show a live status table of all workers in this session.

    Returns:
        Formatted table with worker ID, description, role, status,
        step count, and elapsed time.
    """
    workers = _POOL.list_workers()
    if not workers:
        return "No workers spawned yet."

    lines = ["Workers:"]
    lines.append(f"{'ID':10} {'Description':30} {'Role':10} {'Status':12} {'Steps':6} {'Elapsed':8}")
    lines.append("-" * 80)
    for w in workers:
        lines.append(
            f"{w['id'][:8]:10} {w['description'][:28]:30} {w['role']:10} "
            f"{w['status']:12} {w['steps']:6} {w['elapsed_s']:5}s"
        )
    return "\n".join(lines)


@tool
async def team_create(name: str, description: str) -> str:
    """Create a named team group for organising related workers.

    Workers spawned with team=name will be tagged to this group.
    Use team_delete to stop all workers in the group at once.

    Args:
        name: Short identifier for the team (e.g. 'auth-fix-team').
        description: What this team is working on.

    Returns:
        Confirmation.
    """
    return f"Team '{name}' created: {description}. Tag workers with team='{name}' when calling worker_spawn."


@tool
async def team_delete(name: str) -> str:
    """Stop all workers in a named team group and remove the group.

    Args:
        name: Team name to delete (matches the team= parameter used in worker_spawn).

    Returns:
        Summary of stopped workers.
    """
    worker_ids = _POOL.get_workers_by_team(name)
    if not worker_ids:
        return f"Team '{name}' has no active workers (or does not exist)."
    results = [_POOL.stop(wid) for wid in worker_ids]
    return f"Team '{name}' deleted. Stopped {len(results)} worker(s):\n" + "\n".join(results)


# ── Exported list for graph.py ───────────────────────────────
TEAM_TOOLS = [worker_spawn, worker_message, worker_stop, team_status, team_create, team_delete]
