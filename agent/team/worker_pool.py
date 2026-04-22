"""
Async worker pool for the Agent Teams system.

Each worker is an asyncio Task running its own LLM loop.
Workers push TaskNotification objects to notification_queue when done.
The coordinator polls the queue and injects notifications into its stream.
"""
import asyncio
import logging
import os
import random
import string
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Role → single-char prefix for human-readable worker IDs (CC-style)
_ROLE_PREFIX = {
    "explorer":  "e",
    "architect": "a",
    "coder":     "c",
    "reviewer":  "r",
    "qa":        "q",
    "lead":      "l",
    "general":   "g",
}
_ID_ALPHABET = string.ascii_lowercase + string.digits  # 36-char set

def _generate_worker_id(role: str) -> str:
    """Generate a prefixed, human-readable worker ID.

    Format: <role-prefix>-<8 random chars>
    E.g.: c-k7mn2pxz  (coder), r-a9bq3wef  (reviewer)

    Matches CC's prefix-based task ID scheme — easier to read in logs
    and team_status output than bare UUIDs.
    """
    prefix = _ROLE_PREFIX.get(role, "g")
    suffix = "".join(random.choices(_ID_ALPHABET, k=8))
    return f"{prefix}-{suffix}"

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

MAX_WORKERS = int(os.getenv("SHADOWDEV_MAX_WORKERS", "50"))

_ROLE_PROMPTS = {
    "explorer": (
        "You are a read-only code explorer. Your job is to investigate, search, and understand "
        "the codebase — never to modify it. Use file_read, code_search, grep_search, "
        "lsp_diagnostics, and code_quality. Report findings with specific file paths, line "
        "numbers, and type signatures. Write durable findings to the shared scratchpad when "
        "instructed. Do NOT modify any files under any circumstances."
    ),
    "architect": (
        "You are a software architect. Analyse the codebase structure, identify design problems, "
        "and produce clear technical recommendations. Do NOT write implementation code — your "
        "output is a design document with file paths, module boundaries, and dependency graphs. "
        "Write findings to the scratchpad."
    ),
    "coder": (
        "You are a precise software engineer. Implement the task exactly as specified. "
        "Steps: (1) read the relevant files before editing, (2) make targeted, minimal changes — "
        "fix the root cause, not symptoms, (3) run tests for the changed module and fix any "
        "failures, (4) commit the changes, (5) report what changed, test results, and commit hash. "
        "Do not break existing tests. Do not refactor beyond the task scope."
    ),
    "reviewer": (
        "You are a senior code reviewer. Your job is to PROVE the code works — not to rubber-stamp it. "
        "Steps: (1) read every changed file, (2) run lsp_diagnostics — investigate every error, "
        "do not dismiss any as 'unrelated' without evidence, (3) run tests with the feature enabled "
        "and note all failures, (4) check edge cases and error paths the implementation may have "
        "missed. First line of response MUST be 'PASSED ✅' or 'FAILED ❌'. Then list specific "
        "issues with file:line references. Be skeptical — if something looks off, dig in."
    ),
    "qa": (
        "You are a QA engineer focused on edge cases and regressions. Run the full test suite, "
        "check for regressions in modules that depend on the changed code, test error paths and "
        "boundary conditions. Report: (1) which tests pass/fail, (2) uncovered edge cases, "
        "(3) behaviour that looks wrong even if no test catches it. "
        "First line MUST be 'PASSED ✅' or 'FAILED ❌'."
    ),
    "lead": (
        "You are a sub-team lead. Coordinate the workers under you to accomplish the given goal. "
        "Break the goal into subtasks, delegate to workers with the right roles, synthesize "
        "results, and report a complete summary. Never fabricate worker results — wait for their "
        "notifications. Never say 'based on your findings' — synthesize the findings yourself "
        "into a specific spec before delegating."
    ),
    "general": (
        "You are a general-purpose software engineering agent. Research, analyse, and implement "
        "tasks as needed. Be thorough, precise, and concise. Fix root causes, not symptoms. "
        "Read files before editing. Run tests after changes. Commit your work."
    ),
}


def _create_worker_llm(streaming: bool = False):
    """Create an LLM instance for a worker (fast model)."""
    from agent.nodes import _create_llm
    return _create_llm(streaming=streaming, temperature=0.2, fast=True)


@dataclass
class WorkerEntry:
    id: str
    description: str
    role: str
    task: asyncio.Task
    status: str = "running"
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    steps: int = 0
    team: Optional[str] = None
    pending_messages: asyncio.Queue = field(default_factory=asyncio.Queue)


def _add_child_abort(parent_task: asyncio.Task, child_task: asyncio.Task) -> None:
    """Cancel child task when parent task completes/is cancelled."""
    ref: weakref.ref = weakref.ref(child_task)

    def on_parent_done(t: asyncio.Task) -> None:
        c = ref()
        if c is not None and not c.done():
            c.cancel()

    parent_task.add_done_callback(on_parent_done)


class WorkerPool:
    """Registry and runner for async agent workers."""

    def __init__(self):
        self._workers: dict[str, WorkerEntry] = {}
        self.notification_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._worker_queues: dict[str, asyncio.Queue] = {}  # worker_id → dedicated notification queue
        self._workers_lock = asyncio.Lock()  # protects _workers mutations

    async def spawn(
        self,
        prompt: str,
        role: str,
        tools: list,
        description: str,
        max_steps: int = 30,
        team: Optional[str] = None,
    ) -> str:
        """Spawn a new worker. Returns role-prefixed worker ID."""
        if len(self._workers) >= MAX_WORKERS:
            raise RuntimeError(
                f"Worker limit reached ({MAX_WORKERS}). Stop existing workers before spawning more."
            )
        worker_id = _generate_worker_id(role)
        llm = _create_worker_llm()
        task = asyncio.create_task(
            self._worker_loop(worker_id, prompt, role, tools, max_steps, llm),
            name=f"worker-{worker_id[:8]}",
        )
        entry = WorkerEntry(
            id=worker_id,
            description=description,
            role=role,
            task=task,
            team=team,
        )
        async with self._workers_lock:
            self._workers[worker_id] = entry
        logger.info(f"[pool] spawned worker {worker_id[:8]} role={role}")
        return worker_id

    def send_message(self, worker_id: str, message: str) -> str:
        """Queue a message to a running worker."""
        entry = self._workers.get(worker_id)
        if entry is None:
            return f"Worker {worker_id[:8]} not found."
        if entry.status != "running":
            return f"Worker {worker_id[:8]} is {entry.status} — cannot send message."
        entry.pending_messages.put_nowait(message)
        logger.info(f"[pool] queued message to {worker_id[:8]}")
        return f"Message queued for worker {worker_id[:8]}."

    def stop(self, worker_id: str) -> str:
        """Cancel a running worker."""
        entry = self._workers.get(worker_id)
        if entry is None:
            return f"Worker {worker_id[:8]} not found."
        if not entry.task.done():
            entry.task.cancel()
        entry.status = "stopped"
        # Remove under lock — best-effort (sync context, lock may not be held)
        self._workers.pop(worker_id, None)
        logger.info(f"[pool] stopped worker {worker_id[:8]}")
        return f"Worker {worker_id[:8]} stopped."

    def list_workers(self) -> list[dict]:
        """Return summary of all workers."""
        result = []
        for w in self._workers.values():
            elapsed = (datetime.now(timezone.utc) - w.start_time).seconds
            result.append({
                "id": w.id,
                "description": w.description,
                "role": w.role,
                "status": w.status,
                "steps": w.steps,
                "elapsed_s": elapsed,
                "team": w.team,
            })
        return result

    def get_workers_by_team(self, team: str) -> list[str]:
        # Snapshot values under lock (sync helper — caller holds no lock)
        workers_snapshot = list(self._workers.values())
        return [w.id for w in workers_snapshot if w.team == team]

    def get_all_workers(self) -> list[str]:
        """Return a snapshot list of all current worker IDs."""
        return list(self._workers.keys())

    async def _worker_loop(
        self,
        worker_id: str,
        prompt: str,
        role: str,
        tools: list,
        max_steps: int,
        llm=None,
    ) -> None:
        entry = self._workers[worker_id]
        system_prompt = _ROLE_PROMPTS.get(role, _ROLE_PROMPTS["general"])

        try:
            if llm is None:
                llm = _create_worker_llm()
            llm_with_tools = llm.bind_tools(tools) if tools else llm
            tool_map = {t.name: t for t in tools}

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]

            for step in range(max_steps):
                entry.steps = step + 1

                while not entry.pending_messages.empty():
                    msg = entry.pending_messages.get_nowait()
                    messages.append(HumanMessage(content=msg))
                    logger.debug(f"[worker {worker_id[:8]}] ingested coordinator message")

                from agent.nodes import _invoke_with_retry
                response = await _invoke_with_retry(llm_with_tools, messages)
                messages.append(response)

                if not getattr(response, "tool_calls", None):
                    if entry.pending_messages.empty():
                        entry.status = "completed"
                        await self._push_notification(
                            worker_id, "completed",
                            response.content or "(no output)"
                        )
                        return
                    continue

                tool_results = await asyncio.gather(*[
                    self._invoke_tool(tool_map, tc)
                    for tc in response.tool_calls
                ], return_exceptions=True)

                for tc, result in zip(response.tool_calls, tool_results):
                    content = (
                        f"Error: {result}" if isinstance(result, BaseException)
                        else truncate_output(str(result))
                    )
                    messages.append(ToolMessage(
                        content=content,
                        tool_call_id=tc.get("id", f"call_{step}"),
                        name=tc.get("name", "unknown"),
                    ))

            entry.status = "completed"
            await self._push_notification(
                worker_id, "completed",
                f"(reached max_steps={max_steps})"
            )

        except asyncio.CancelledError:
            entry.status = "stopped"
            await self._push_notification(worker_id, "killed", "Worker was stopped.")
        except Exception as exc:
            entry.status = "failed"
            logger.error(f"[worker {worker_id[:8]}] exception: {exc}", exc_info=True)
            await self._push_notification(worker_id, "failed", str(exc))

    async def _invoke_tool(self, tool_map: dict, tc: dict) -> str:
        name = tc.get("name", "")
        args = tc.get("args", {})
        tool = tool_map.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            if hasattr(tool, "ainvoke"):
                return await tool.ainvoke(args)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, tool.invoke, args)
        except Exception as e:
            return f"Error in {name}: {e}"

    async def _push_notification(self, worker_id: str, status: str, result: str) -> None:
        entry = self._workers.get(worker_id)
        desc = entry.description if entry else worker_id[:8]
        notification = (
            f"<task-notification>\n"
            f"<task-id>{worker_id}</task-id>\n"
            f"<status>{status}</status>\n"
            f"<summary>Worker \"{desc}\" {status}</summary>\n"
            f"<result>{result[:2000]}</result>\n"
            f"</task-notification>"
        )
        # Feed dedicated per-worker queue first (for wait_for_worker consumers)
        if worker_id in self._worker_queues:
            try:
                self._worker_queues[worker_id].put_nowait((status, notification))
            except asyncio.QueueFull:
                pass  # overflow: drop (consumer timed out)

        # Also push to the global queue for external consumers (e.g. coordinator)
        try:
            self.notification_queue.put_nowait(notification)
        except asyncio.QueueFull:
            # Queue is full — drop oldest notification and warn
            try:
                dropped = self.notification_queue.get_nowait()
                logger.error(
                    "[pool] notification_queue full (maxsize=%d) — dropped oldest notification; "
                    "coordinator may hang waiting for lost task-complete signal. Dropped: %s",
                    self.notification_queue.maxsize,
                    dropped[:200],
                )
                self.notification_queue.put_nowait(notification)
            except Exception:
                logger.critical(
                    "[pool] notification_queue full and could not recover — notification LOST: %s",
                    notification[:200],
                )
        logger.info(f"[pool] notification pushed for {worker_id[:8]}: {status}")


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~15)
# Technique :
#   - NEVER create files unless theyre absolutely necessary for achieving
#   your goal
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~16)
# Technique :
#   - NEVER proactively create documentation files (*
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : template_literal (line ~24)
# Technique :
#   You are a file search specialist for Claude Code, Anthropic's official
#   CLI for Claude
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~27)
# Technique :
#   This is a READ-ONLY exploration task
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~36)
# Technique :
#   Your role is EXCLUSIVELY to search and analyze existing code
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~48)
# Technique :
#   - NEVER use ${BASH_TOOL_NAME} for: mkdir touch rm cp mv git add git
#   commit npm install pip install or any file creation/
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~50)
# Technique :
#   - Communicate your final report directly as a regular message - do NOT
#   attempt to create files
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~52)
# Technique :
#   NOTE: You are meant to be a fast agent that returns output as quickly as
#   possible
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : agents
# CC source : instruction_line (line ~24)
# Technique :
#   This is a READ-ONLY planning task
# [/PromptIntel] ------------------------------------------------------
