"""
Async worker pool for the Agent Teams system.

Each worker is an asyncio Task running its own LLM loop.
Workers push TaskNotification objects to notification_queue when done.
The coordinator polls the queue and injects notifications into its stream.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

_ROLE_PROMPTS = {
    "explorer": (
        "You are a read-only code explorer. Search, read, and analyse the codebase. "
        "Report findings clearly. Do NOT modify any files."
    ),
    "coder": (
        "You are a software engineer. Implement the task as specified. "
        "Read files before editing. Run tests after changes. Commit your work."
    ),
    "reviewer": (
        "You are a senior code reviewer. Run LSP diagnostics and tests on the changed files. "
        "Return 'PASSED ✅' or 'FAILED ❌' as the first line, then list specific issues."
    ),
    "general": (
        "You are a general-purpose software engineering agent. "
        "Research, analyse, and implement tasks. Be thorough and concise."
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
    start_time: datetime = field(default_factory=datetime.utcnow)
    steps: int = 0
    team: Optional[str] = None
    pending_messages: asyncio.Queue = field(default_factory=asyncio.Queue)


class WorkerPool:
    """Registry and runner for async agent workers."""

    def __init__(self):
        self._workers: dict[str, WorkerEntry] = {}
        self.notification_queue: asyncio.Queue = asyncio.Queue()

    async def spawn(
        self,
        prompt: str,
        role: str,
        tools: list,
        description: str,
        max_steps: int = 30,
        team: Optional[str] = None,
    ) -> str:
        """Spawn a new worker. Returns worker UUID."""
        worker_id = str(uuid.uuid4())
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
        logger.info(f"[pool] stopped worker {worker_id[:8]}")
        return f"Worker {worker_id[:8]} stopped."

    def list_workers(self) -> list[dict]:
        """Return summary of all workers."""
        result = []
        for w in self._workers.values():
            elapsed = (datetime.utcnow() - w.start_time).seconds
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
        return [w.id for w in self._workers.values() if w.team == team]

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

                response = await llm_with_tools.ainvoke(messages)
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
            loop = asyncio.get_event_loop()
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
        await self.notification_queue.put(notification)
        logger.info(f"[pool] notification pushed for {worker_id[:8]}: {status}")
