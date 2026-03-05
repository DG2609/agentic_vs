"""
Hook system for pre/post tool execution.

Supports two hook types:
1. Shell hooks — run a subprocess, parse JSON stdout
2. Python hooks — direct callable

Hooks match tool names via fnmatch glob patterns (e.g. "file_*", "*", "terminal_exec").
"""
import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    """Result from a hook execution."""
    block: bool = False                         # PreToolUse: prevent tool execution
    reason: str = ""                            # Why blocked
    modified_args: Optional[dict] = None        # PreToolUse: override tool args
    modified_output: Optional[str] = None       # PostToolUse: override tool output
    inject_message: Optional[str] = None        # Add system message after


@dataclass
class ToolHook:
    """A registered hook."""
    event: str                  # "pre_tool_use" or "post_tool_use"
    pattern: str                # fnmatch glob for tool name
    handler: Optional[Callable] = None   # Python callable (async or sync)
    command: Optional[str] = None        # Shell command
    name: str = ""              # Optional name for logging

    def matches(self, tool_name: str) -> bool:
        """Check if this hook matches the given tool name."""
        return fnmatch(tool_name, self.pattern)


# ── Global registries ────────────────────────────────────────
PRE_TOOL_HOOKS: list[ToolHook] = []
POST_TOOL_HOOKS: list[ToolHook] = []


def register_hook(
    event: str,
    pattern: str,
    handler: Optional[Callable] = None,
    command: Optional[str] = None,
    name: str = "",
) -> None:
    """Register a hook for pre or post tool use.

    Args:
        event: "pre_tool_use" or "post_tool_use"
        pattern: fnmatch glob for tool name matching
        handler: Python callable (receives tool_name, tool_args, [output])
        command: Shell command to execute (receives JSON on stdin)
        name: Optional name for logging
    """
    if event not in ("pre_tool_use", "post_tool_use"):
        raise ValueError(f"event must be 'pre_tool_use' or 'post_tool_use', got '{event}'")
    if not handler and not command:
        raise ValueError("Must provide either handler or command")

    hook = ToolHook(event=event, pattern=pattern, handler=handler, command=command, name=name or pattern)

    if event == "pre_tool_use":
        PRE_TOOL_HOOKS.append(hook)
    else:
        POST_TOOL_HOOKS.append(hook)

    logger.info(f"[hooks] Registered {event} hook '{hook.name}' for pattern '{pattern}'")


def clear_hooks() -> None:
    """Clear all registered hooks."""
    PRE_TOOL_HOOKS.clear()
    POST_TOOL_HOOKS.clear()


async def _run_shell_hook(hook: ToolHook, payload: dict) -> HookResult:
    """Execute a shell hook command and parse JSON output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *hook.command.split(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_data = json.dumps(payload).encode()
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin_data), timeout=10)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.warning(f"[hooks] Shell hook '{hook.name}' exited {proc.returncode}: {err}")
            return HookResult()

        if not stdout.strip():
            return HookResult()

        data = json.loads(stdout.decode(errors="replace"))
        return HookResult(
            block=data.get("block", False),
            reason=data.get("reason", ""),
            modified_args=data.get("modified_args"),
            modified_output=data.get("modified_output"),
            inject_message=data.get("inject_message"),
        )
    except asyncio.TimeoutError:
        logger.warning(f"[hooks] Shell hook '{hook.name}' timed out (10s)")
        return HookResult()
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[hooks] Shell hook '{hook.name}' error: {e}")
        return HookResult()


async def _run_python_hook(hook: ToolHook, *args) -> HookResult:
    """Execute a Python callable hook."""
    try:
        result = hook.handler(*args)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, HookResult):
            return result
        if isinstance(result, dict):
            return HookResult(**{k: v for k, v in result.items() if k in HookResult.__dataclass_fields__})
        return HookResult()
    except Exception as e:
        logger.warning(f"[hooks] Python hook '{hook.name}' error: {e}")
        return HookResult()


async def run_pre_hooks(tool_name: str, tool_args: dict) -> HookResult:
    """Run all matching pre-tool-use hooks.

    Returns a merged HookResult. If any hook blocks, the tool is blocked.
    The last non-None modified_args wins.
    """
    merged = HookResult()

    for hook in PRE_TOOL_HOOKS:
        if not hook.matches(tool_name):
            continue

        if hook.command:
            result = await _run_shell_hook(hook, {
                "event": "pre_tool_use",
                "tool_name": tool_name,
                "tool_args": tool_args,
            })
        elif hook.handler:
            result = await _run_python_hook(hook, tool_name, tool_args)
        else:
            continue

        if result.block:
            merged.block = True
            merged.reason = result.reason or f"Blocked by hook '{hook.name}'"
        if result.modified_args is not None:
            merged.modified_args = result.modified_args
        if result.inject_message is not None:
            merged.inject_message = result.inject_message

    return merged


async def run_post_hooks(tool_name: str, tool_args: dict, output: str) -> HookResult:
    """Run all matching post-tool-use hooks.

    Returns a merged HookResult. The last non-None modified_output wins.
    """
    merged = HookResult()

    for hook in POST_TOOL_HOOKS:
        if not hook.matches(tool_name):
            continue

        if hook.command:
            result = await _run_shell_hook(hook, {
                "event": "post_tool_use",
                "tool_name": tool_name,
                "tool_args": tool_args,
                "output": output[:5000],  # limit output sent to hooks
            })
        elif hook.handler:
            result = await _run_python_hook(hook, tool_name, tool_args, output)
        else:
            continue

        if result.modified_output is not None:
            merged.modified_output = result.modified_output
        if result.inject_message is not None:
            merged.inject_message = result.inject_message

    return merged


def load_hooks_from_config(hooks_config: list[dict]) -> None:
    """Load hooks from a list of config dicts.

    Each dict should have:
        - event: "pre_tool_use" or "post_tool_use"
        - pattern: fnmatch glob for tool names
        - command: shell command to run
        - name (optional): human-readable name
    """
    for entry in hooks_config:
        event = entry.get("event", "")
        pattern = entry.get("pattern", "*")
        command = entry.get("command", "")
        name = entry.get("name", command or pattern)

        if event not in ("pre_tool_use", "post_tool_use"):
            logger.warning(f"[hooks] Skipping invalid event '{event}' in hook config")
            continue
        if not command:
            logger.warning(f"[hooks] Skipping hook with no command: {entry}")
            continue

        register_hook(event=event, pattern=pattern, command=command, name=name)


def load_hooks_from_file(filepath: str) -> None:
    """Load hooks from a JSON file."""
    if not filepath or not os.path.isfile(filepath):
        return

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            hooks_config = json.load(f)
        if isinstance(hooks_config, list):
            load_hooks_from_config(hooks_config)
            logger.info(f"[hooks] Loaded {len(hooks_config)} hook(s) from {filepath}")
        else:
            logger.warning(f"[hooks] Expected list in {filepath}, got {type(hooks_config).__name__}")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[hooks] Failed to load hooks from {filepath}: {e}")
