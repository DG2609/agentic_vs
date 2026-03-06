"""
Headless / CI mode runner for ShadowDev.

Executes a single prompt non-interactively and exits with a proper exit code.
Used by cli.py when --prompt/-p is supplied.
"""

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

import config


@dataclass
class HeadlessResult:
    success: bool
    messages: list[str] = field(default_factory=list)    # reply_to_user contents
    tool_calls: list[dict] = field(default_factory=list)   # tool invocations
    errors: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    exit_code: int = 0


async def run_headless(
    prompt: str,
    session_id: Optional[str] = None,
    agent: str = "planner",
    output_format: str = "text",
    timeout: Optional[int] = None,
    allowed_tools: Optional[list[str]] = None,
) -> HeadlessResult:
    """
    Run the agent in headless mode on a single prompt.

    Args:
        prompt:        The user prompt to execute.
        session_id:    Thread ID for multi-turn context (default: new UUID).
        agent:         Starting agent — "planner" or "coder".
        output_format: "text" | "json" | "stream-json".
        timeout:       Max execution time in seconds (None = no limit).
        allowed_tools: Whitelist of tool names (None = all tools allowed).

    Returns:
        HeadlessResult with success flag, messages, tool_calls, errors, exit_code.
    """
    # Import here to avoid circular imports at module load time
    from agent.graph import build_graph

    thread_id = session_id or str(uuid.uuid4())
    graph = build_graph(checkpointer=MemorySaver())

    graph_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100,
    }
    input_state: dict = {
        "messages": [HumanMessage(content=prompt)],
        "workspace": config.WORKSPACE_DIR,
        "active_agent": agent,
    }

    result = HeadlessResult(success=False)
    start = time.time()
    printed_replies: set[str] = set()

    async def _stream() -> None:
        async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
            kind = event.get("event", "")
            run_id = event.get("run_id", "")

            # ── Tool start ───────────────────────────────────
            if kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                args = event.get("data", {}).get("input", {})

                # Filter by allowed_tools whitelist
                if allowed_tools and tool_name not in allowed_tools:
                    continue

                # Collect reply_to_user messages
                if tool_name == "reply_to_user" and run_id not in printed_replies:
                    msg = _extract_reply(args)
                    if msg:
                        result.messages.append(msg)
                        printed_replies.add(run_id)
                        _emit_message(output_format, msg)

                tc = {
                    "tool": tool_name,
                    "args": args if isinstance(args, dict) else {},
                    "run_id": run_id,
                }
                result.tool_calls.append(tc)
                if output_format == "stream-json":
                    _emit_json({"type": "tool_start", **tc})

            # ── Tool end ─────────────────────────────────────
            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                args = event.get("data", {}).get("input", {})

                # Catch reply_to_user from end event in case start was missed
                if tool_name == "reply_to_user" and run_id not in printed_replies:
                    msg = _extract_reply(args)
                    if msg:
                        result.messages.append(msg)
                        printed_replies.add(run_id)
                        _emit_message(output_format, msg)

                if output_format == "stream-json":
                    out = event.get("data", {}).get("output", "")
                    _emit_json({"type": "tool_end", "tool": tool_name, "run_id": run_id,
                                "output": str(out)[:500]})

    try:
        if timeout and timeout > 0:
            await asyncio.wait_for(_stream(), timeout=float(timeout))
        else:
            await _stream()
        result.success = True
        result.exit_code = 0

    except asyncio.TimeoutError:
        msg = f"Execution timed out after {timeout}s"
        result.errors.append(msg)
        result.exit_code = 124  # standard POSIX timeout exit code
        if output_format == "stream-json":
            _emit_json({"type": "error", "message": msg})
        else:
            print(f"[timeout] {msg}", file=sys.stderr)

    except Exception as exc:
        msg = str(exc)
        result.errors.append(msg)
        result.exit_code = 1
        if output_format == "stream-json":
            _emit_json({"type": "error", "message": msg})
        else:
            print(f"[error] {msg}", file=sys.stderr)

    finally:
        result.elapsed = time.time() - start

    return result


# ── Helpers ───────────────────────────────────────────────────

def _extract_reply(args) -> str:
    """Pull the message string out of reply_to_user args."""
    if isinstance(args, dict):
        return args.get("message", "")
    if isinstance(args, str):
        try:
            return json.loads(args).get("message", "")
        except Exception:
            pass
    return ""


def _emit_message(output_format: str, msg: str) -> None:
    """Print a reply_to_user message in the chosen format."""
    if output_format == "stream-json":
        _emit_json({"type": "message", "content": msg})
    elif output_format == "text":
        print(msg, flush=True)
    # json format: collected and printed at the end by cli.py


def _emit_json(obj: dict) -> None:
    """Print one JSON object per line (stream-json format)."""
    print(json.dumps(obj), flush=True)


def print_result(result: HeadlessResult, output_format: str) -> None:
    """
    Print the final result in the chosen format.
    Called by cli.py after run_headless() returns.
    stream-json: already streamed during execution, print summary line.
    json: print single JSON object.
    text: already printed messages during execution, print errors.
    """
    if output_format == "json":
        print(json.dumps({
            "success": result.success,
            "messages": result.messages,
            "tool_calls": result.tool_calls,
            "errors": result.errors,
            "elapsed_seconds": round(result.elapsed, 2),
        }))
    elif output_format == "stream-json":
        _emit_json({
            "type": "done",
            "success": result.success,
            "exit_code": result.exit_code,
            "elapsed_seconds": round(result.elapsed, 2),
            "error_count": len(result.errors),
        })
    else:
        # text: print errors to stderr
        for err in result.errors:
            print(f"[error] {err}", file=sys.stderr)
