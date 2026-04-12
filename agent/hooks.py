"""
Hook system for pre/post tool execution and lifecycle events.

Supports two hook types:
1. Shell hooks — run a subprocess, parse JSON stdout
2. Python hooks — direct callable

Tool hooks match tool names via fnmatch glob patterns.
Lifecycle hooks fire on events like session_start, stop, user_prompt_submit, etc.

Valid lifecycle events:
    session_start       — fired at start of interactive/headless session
    session_end         — fired when session exits
    user_prompt_submit  — fired before each user prompt; shell hook can return
                          {"modified_prompt": "..."} to transform the prompt
    stop                — fired after agent completes a run
    subagent_start      — fired before a subagent task executes
    subagent_stop       — fired after a subagent task completes
    pre_compact         — fired before conversation compaction (summarize_node)
    post_compact        — fired after conversation compaction
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

_LIFECYCLE_EVENTS = frozenset({
    "session_start", "session_end", "user_prompt_submit", "stop",
    "subagent_start", "subagent_stop", "pre_compact", "post_compact",
})
_TOOL_EVENTS = frozenset({"pre_tool_use", "post_tool_use"})


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
    """A registered tool hook (pre_tool_use / post_tool_use)."""
    event: str                  # "pre_tool_use" or "post_tool_use"
    pattern: str                # fnmatch glob for tool name
    handler: Optional[Callable] = None   # Python callable (async or sync)
    command: Optional[str] = None        # Shell command
    name: str = ""              # Optional name for logging

    def matches(self, tool_name: str) -> bool:
        """Check if this hook matches the given tool name."""
        return fnmatch(tool_name, self.pattern)


@dataclass
class LifecycleHook:
    """A registered lifecycle hook (session_start, stop, subagent_*, etc.)."""
    event: str                          # one of _LIFECYCLE_EVENTS
    handler: Optional[Callable] = None  # Python callable (async or sync)
    command: Optional[str] = None       # Shell command
    name: str = ""


# ── Global registries ────────────────────────────────────────
PRE_TOOL_HOOKS: list[ToolHook] = []
POST_TOOL_HOOKS: list[ToolHook] = []
LIFECYCLE_HOOKS: list[LifecycleHook] = []


def register_hook(
    event: str,
    pattern: str = "*",
    handler: Optional[Callable] = None,
    command: Optional[str] = None,
    name: str = "",
) -> None:
    """Register a hook for tool use or lifecycle events.

    Args:
        event: "pre_tool_use" | "post_tool_use" | lifecycle event name
        pattern: fnmatch glob for tool name (ignored for lifecycle hooks)
        handler: Python callable (async or sync)
        command: Shell command (receives JSON payload on stdin)
        name: Optional name for logging
    """
    all_events = _TOOL_EVENTS | _LIFECYCLE_EVENTS
    if event not in all_events:
        raise ValueError(f"event must be one of: {sorted(all_events)}, got '{event}'")
    if not handler and not command:
        raise ValueError("Must provide either handler or command")

    if event in _LIFECYCLE_EVENTS:
        hook = LifecycleHook(event=event, handler=handler, command=command, name=name or event)
        LIFECYCLE_HOOKS.append(hook)
        logger.info("[hooks] Registered lifecycle hook '%s' for event '%s'", hook.name, event)
    else:
        hook = ToolHook(event=event, pattern=pattern, handler=handler, command=command, name=name or pattern)
        if event == "pre_tool_use":
            PRE_TOOL_HOOKS.append(hook)
        else:
            POST_TOOL_HOOKS.append(hook)
        logger.info("[hooks] Registered %s hook '%s' for pattern '%s'", event, hook.name, pattern)


def clear_hooks() -> None:
    """Clear all registered hooks (tool + lifecycle)."""
    PRE_TOOL_HOOKS.clear()
    POST_TOOL_HOOKS.clear()
    LIFECYCLE_HOOKS.clear()


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

        if proc.returncode == 2:
            # Exit code 2 = block the tool execution
            err = stderr.decode(errors="replace").strip()
            reason = err or f"hook '{hook.name}' exited with code 2"
            logger.info("[hooks] Shell hook '%s' blocked tool (exit 2): %s", hook.name, reason)
            return HookResult(block=True, reason=reason)

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
            # Support {"blocked": True, "reason": "..."} shorthand from Python hooks
            if result.get("blocked"):
                reason = result.get("reason", f"blocked by hook '{hook.name}'")
                return HookResult(block=True, reason=reason)
            # Map remaining known fields
            known = {k: v for k, v in result.items() if k in HookResult.__dataclass_fields__}
            return HookResult(**known)
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


async def run_lifecycle_hook(event: str, payload: Optional[dict] = None) -> Optional[str]:
    """Fire all lifecycle hooks matching `event`.

    For 'user_prompt_submit' hooks: returns the last modified_prompt from any hook,
    or None if no hook modified it.
    For all other events: returns None (fire-and-forget notification).
    """
    modified_prompt: Optional[str] = None
    data = payload or {}

    for hook in LIFECYCLE_HOOKS:
        if hook.event != event:
            continue

        try:
            if hook.command:
                full_payload = {"event": event, **data}
                result_data = {}
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *hook.command.split(),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdin_bytes = json.dumps(full_payload).encode()
                    stdout, stderr = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout=10)
                    if stdout.strip():
                        result_data = json.loads(stdout.decode(errors="replace"))
                except asyncio.TimeoutError:
                    logger.warning("[hooks] Lifecycle shell hook '%s' timed out", hook.name)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("[hooks] Lifecycle shell hook '%s' error: %s", hook.name, e)

                if event == "user_prompt_submit" and result_data.get("modified_prompt"):
                    modified_prompt = result_data["modified_prompt"]

            elif hook.handler:
                result = hook.handler(event, data)
                if asyncio.iscoroutine(result):
                    result = await result
                if event == "user_prompt_submit" and isinstance(result, dict):
                    if result.get("modified_prompt"):
                        modified_prompt = result["modified_prompt"]

        except Exception as e:
            logger.warning("[hooks] Lifecycle hook '%s' error: %s", hook.name, e)

    return modified_prompt


def load_hooks_from_config(hooks_config: list[dict]) -> None:
    """Load hooks from a list of config dicts.

    Each dict should have:
        - event: "pre_tool_use" | "post_tool_use" | lifecycle event name
        - pattern: fnmatch glob for tool names (tool events only; ignored for lifecycle)
        - command: shell command to run
        - name (optional): human-readable name
    """
    all_events = _TOOL_EVENTS | _LIFECYCLE_EVENTS
    for entry in hooks_config:
        event = entry.get("event", "")
        pattern = entry.get("pattern", "*")
        command = entry.get("command", "")
        name = entry.get("name", command or pattern)

        if event not in all_events:
            logger.warning("[hooks] Skipping unknown event '%s' (valid: %s)", event, sorted(all_events))
            continue
        if not command:
            logger.warning("[hooks] Skipping hook with no command: %s", entry)
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
