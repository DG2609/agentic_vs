"""
LangGraph StateGraph — the core agent workflow.

Graph structure:
    __start__ → agent → (tool_calls?) → tool_node → agent → ... → __end__
                   ↓ (no tool calls)
                   → check_compaction → summarize → __end__
                                      → __end__

Upgraded with:
- Token-based compaction trigger (replaces naive message count)
- New LSP tools (references, hover, symbols, diagnostics)
- All tool outputs go through universal truncation
- Smart model routing (fast model for subagents/summarization)
- Parallel tool call guidance in system prompt
- Git tools suite (status, diff, log, show, blame, add, commit, branch, stash)
- Test runner (pytest/jest/vitest/cargo/go), atomic batch edit, memory,
  code quality, dependency graph tools
"""

import asyncio
import json
import logging
from functools import partial
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import ToolMessage
from models.state import AgentState
from agent.nodes import (
    agent_node, summarize_node, get_llm,
    is_context_overflow,
)
from agent.tools.code_search import code_search, grep_search, batch_read
from agent.tools.file_ops import (
    file_read, file_write, file_list, file_edit, glob_search, file_edit_batch,
)
from agent.tools.terminal import terminal_exec
from agent.tools.code_analyzer import code_analyze
from agent.tools.semantic import semantic_search, index_codebase
from agent.tools.web import webfetch
from agent.tools.lsp import (
    lsp_definition, lsp_references, lsp_hover, lsp_symbols, lsp_diagnostics,
)
from agent.tools.lsp_tools import lsp_go_to_definition, lsp_find_references
from agent.tools.handoff import handoff_to_coder, handoff_to_planner
from agent.tools.communication import reply_to_user
from agent.subagents import task_explore, task_explore_parallel, task_general, task_review
from agent.tools.todo import todo_read, todo_write
from agent.tools.plan import plan_enter, plan_exit
from agent.tools.question import question
from agent.tools.websearch import web_search
from agent.tools.git import (
    # Read-only: available to both Planner and Coder
    git_status, git_diff, git_log, git_show, git_blame,
    # Write: Coder only (local)
    git_add, git_commit, git_branch, git_stash,
    # Write: Coder only (remote)
    git_push, git_pull, git_fetch, git_merge,
)
from agent.tools.test_runner import run_tests
from agent.tools.memory import memory_save, memory_search, memory_list, memory_delete
from agent.tools.code_quality import code_quality
from agent.tools.dep_graph import dep_graph
from agent.tools.context_build import context_build
from agent.tools.skills import skill_invoke, skill_list, skill_create, hub_search, skill_install, skill_remove
from agent.tools.voice import voice_input
from agent.tools.image_input import image_input
from agent.tools.snapshot_tools import snapshot_list, snapshot_revert, snapshot_info
from agent.tools.context_hub import chub_search, chub_get, chub_annotate, chub_feedback
from agent.team.tools import TEAM_TOOLS
from agent.team.coordinator import is_coordinator_mode, get_coordinator_system_prompt
from agent.tools.github import (
    github_list_issues, github_list_prs, github_get_pr,
    github_create_issue, github_create_pr, github_comment,
)
from agent.tools.gitlab import (
    gitlab_list_issues, gitlab_list_mrs, gitlab_get_mr,
    gitlab_create_issue, gitlab_create_mr, gitlab_comment,
)
from agent.skill_loader import load_skills as _load_skills
from agent.plugin_registry import get_plugin_tools as _get_plugin_tools, list_plugins as _list_installed_plugins
from agent.hooks import (
    run_pre_hooks, run_post_hooks, register_hook,
    load_hooks_from_file, PRE_TOOL_HOOKS, POST_TOOL_HOOKS, LIFECYCLE_HOOKS,
)
from agent.permissions import check_permission
from agent.snapshots import create_snapshot
from agent.context_providers import context_provider_hook
from agent.mcp_client import load_mcp_tools as _load_mcp_tools
import config

logger = logging.getLogger(__name__)

# ── Core tool set (used for skill dedup) ────────────────────
_CORE_TOOLS = [
    code_search, grep_search, batch_read, semantic_search, index_codebase,
    file_read, file_write, file_list, file_edit, file_edit_batch, glob_search,
    terminal_exec, code_analyze, webfetch, web_search,
    lsp_definition, lsp_references, lsp_hover, lsp_symbols, lsp_diagnostics,
    lsp_go_to_definition, lsp_find_references,
    handoff_to_coder, handoff_to_planner, reply_to_user,
    task_explore, task_explore_parallel, task_general, task_review,
    todo_read, todo_write, plan_enter, plan_exit, question,
    git_status, git_diff, git_log, git_show, git_blame,
    git_add, git_commit, git_branch, git_stash,
    git_push, git_pull, git_fetch, git_merge,
    run_tests,
    memory_save, memory_search, memory_list, memory_delete,
    code_quality, dep_graph, context_build,
    skill_invoke, skill_list, skill_create, hub_search, skill_install, skill_remove,
    github_list_issues, github_list_prs, github_get_pr,
    github_create_issue, github_create_pr, github_comment,
    gitlab_list_issues, gitlab_list_mrs, gitlab_get_mr,
    gitlab_create_issue, gitlab_create_mr, gitlab_comment,
    voice_input, image_input,
    snapshot_list, snapshot_revert, snapshot_info,
    chub_search, chub_get, chub_annotate, chub_feedback,
]
_all_core_names = {t.name for t in _CORE_TOOLS}
_planner_skills, _coder_skills = _load_skills(existing_names=_all_core_names)

# ── Load pip-installed plugins (entry_points group "shadowdev.tools") ──────
_existing_names = _all_core_names | {t.name for t in _planner_skills + _coder_skills}
_plugin_planner, _plugin_coder = _get_plugin_tools(existing_names=_existing_names)

# ── Load MCP server tools ────────────────────────────────────
_mcp_planner_tools, _mcp_coder_tools = _load_mcp_tools(config.MCP_SERVERS)


# ── Separate Tools for Swarm Roles ──────────────────────────
PLANNER_TOOLS = [
    # Search & read
    code_search, grep_search, batch_read, semantic_search, index_codebase,
    file_read, glob_search, file_list, code_analyze, webfetch, web_search,
    # Code insight
    code_quality, dep_graph, context_build,
    # LSP
    lsp_definition, lsp_references, lsp_hover, lsp_symbols, lsp_diagnostics,
    lsp_go_to_definition, lsp_find_references,
    # Git (read-only)
    git_status, git_diff, git_log, git_show, git_blame,
    # Memory (cross-session knowledge)
    memory_save, memory_search, memory_list, memory_delete,
    # Agent coordination
    handoff_to_coder, reply_to_user, task_explore, task_explore_parallel,
    todo_read, todo_write, plan_enter, plan_exit, question,
    # Skill system (markdown workflow skills + hub discovery)
    skill_invoke, skill_list, hub_search,
    # Multimodal input
    voice_input, image_input,
    # Snapshot/revert
    snapshot_list, snapshot_revert, snapshot_info,
    # Agent Teams — coordinator tools (coordinator prompt activates their full use)
    *TEAM_TOOLS,
    # Context Hub — curated API docs (68+ services)
    chub_search, chub_get, chub_annotate, chub_feedback,
    # GitHub (read-only)
    github_list_issues, github_list_prs, github_get_pr,
    # GitLab (read-only)
    gitlab_list_issues, gitlab_list_mrs, gitlab_get_mr,
    # External skills (read/both access)
    *_planner_skills,
    # Pip-installed plugins (read/both access)
    *_plugin_planner,
    # MCP server tools (read/both access)
    *_mcp_planner_tools,
]

CODER_TOOLS = PLANNER_TOOLS + [
    # File write
    file_edit, file_edit_batch, file_write, terminal_exec,
    # Testing
    run_tests,
    # Git (write — local)
    git_add, git_commit, git_branch, git_stash,
    # Git (write — remote)
    git_push, git_pull, git_fetch, git_merge,
    # Agent coordination
    handoff_to_planner, task_general, task_review,
    # GitHub (write)
    github_create_issue, github_create_pr, github_comment,
    # GitLab (write)
    gitlab_create_issue, gitlab_create_mr, gitlab_comment,
    # Skill system (create, install, remove skills)
    skill_create, skill_install, skill_remove,
    # External skills (write access)
    *_coder_skills,
    # Pip-installed plugins (write access)
    *_plugin_coder,
    # MCP server tools (write access)
    *_mcp_coder_tools,
]

ALL_TOOLS = list({t.name: t for t in PLANNER_TOOLS + CODER_TOOLS}.values())


# ── Load hooks from config ───────────────────────────────────
if config.HOOKS_FILE:
    load_hooks_from_file(config.HOOKS_FILE)


# ── Register built-in hooks ─────────────────────────────────

# Context providers: expand @file:, @diff, @codebase: in user prompts
register_hook(
    event="user_prompt_submit",
    handler=context_provider_hook,
    name="context_providers",
)

# Snapshot: auto-backup files before write tools modify them
_SNAPSHOT_TOOLS = frozenset({"file_write", "file_edit", "file_edit_batch"})


async def _snapshot_pre_hook(tool_name: str, tool_args: dict):
    """Pre-hook: create file snapshot before write operations."""
    if tool_name not in _SNAPSHOT_TOOLS:
        return None
    file_path = tool_args.get("file_path", "")
    if not file_path:
        # file_edit_batch may have edits list
        edits = tool_args.get("edits", [])
        paths = [e.get("file_path", "") for e in edits if e.get("file_path")]
        if paths:
            try:
                create_snapshot(paths, message=f"{tool_name} (batch: {len(paths)} files)")
            except Exception:
                pass
        return None
    try:
        create_snapshot([file_path], message=f"{tool_name}: {file_path}")
    except Exception:
        pass
    return None


register_hook(
    event="pre_tool_use",
    pattern="file_*",
    handler=_snapshot_pre_hook,
    name="auto_snapshot",
)


class HookedToolNode:
    """Wraps LangGraph ToolNode with pre/post hook support and parallel execution.

    - Pre-hooks run in parallel for all tool calls before execution.
    - Tools execute in parallel via asyncio.gather (order-preserving).
    - Post-hooks run in parallel on all results.
    - A blocked tool returns an error message; others still execute.
    """

    def __init__(self, tools: list):
        self._inner = ToolNode(tools)
        self._tool_map = {t.name: t for t in tools}

    @property
    def _has_hooks(self) -> bool:
        return bool(PRE_TOOL_HOOKS or POST_TOOL_HOOKS)

    async def _invoke_one(self, tool, args: dict) -> str:
        """Invoke a single tool asynchronously, returning a string result."""
        try:
            if hasattr(tool, "ainvoke"):
                result = await tool.ainvoke(args)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, tool.invoke, args)
            if isinstance(result, str):
                return result
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    async def __call__(self, state):
        if not self._has_hooks:
            return await self._inner.ainvoke(state)

        messages = list(state["messages"]) if isinstance(state, dict) else list(state.messages)
        last_msg = messages[-1] if messages else None

        if not last_msg or not getattr(last_msg, "tool_calls", None):
            return await self._inner.ainvoke(state)

        tool_calls = last_msg.tool_calls

        # ── Step 1: Run all pre-hooks in parallel ─────────────────
        pre_results = await asyncio.gather(*[
            run_pre_hooks(tc.get("name", ""), tc.get("args", {}))
            for tc in tool_calls
        ])

        # ── Step 2: Partition blocked vs executable (preserve order) ─
        pending: list = [None] * len(tool_calls)  # (tc_id, tool_name, content)
        to_execute: list = []  # (index, tc_id, tool_name, args, tool)

        for i, (tc, pre_result) in enumerate(zip(tool_calls, pre_results)):
            tool_name = tc.get("name", "")
            tc_id = tc.get("id", "")

            if pre_result.block:
                pending[i] = (tc_id, tool_name, f"[BLOCKED by hook] {pre_result.reason}")
                continue

            effective_args = (
                pre_result.modified_args
                if pre_result.modified_args is not None
                else tc.get("args", {})
            )
            tool = self._tool_map.get(tool_name)
            if tool is None:
                pending[i] = (tc_id, tool_name, f"Unknown tool: {tool_name}")
                continue

            to_execute.append((i, tc_id, tool_name, effective_args, tool))

        # ── Step 2b: Check permissions in parallel ────────────────
        if to_execute:
            perm_results = await asyncio.gather(*[
                check_permission(name, args)
                for _, _, name, args, _ in to_execute
            ])
            filtered = []
            for (i, tc_id, name, args, tool), (allowed, reason) in zip(to_execute, perm_results):
                if not allowed:
                    pending[i] = (tc_id, name, f"[DENIED] {reason}")
                else:
                    filtered.append((i, tc_id, name, args, tool))
            to_execute = filtered

        # ── Step 3: Execute all non-blocked tools in parallel ─────
        if to_execute:
            exec_results = await asyncio.gather(*[
                self._invoke_one(tool, args)
                for _, _, _, args, tool in to_execute
            ], return_exceptions=True)

            for (i, tc_id, tool_name, args, tool), exec_result in zip(to_execute, exec_results):
                if isinstance(exec_result, BaseException):
                    content = f"Error in {tool_name}: {type(exec_result).__name__}: {exec_result}"
                else:
                    content = exec_result
                pending[i] = (tc_id, tool_name, content)

        # ── Step 4: Run all post-hooks in parallel ────────────────
        post_results = await asyncio.gather(*[
            run_post_hooks(tool_name, {}, content)
            for _, tool_name, content in pending
        ])

        # ── Step 5: Build final ToolMessage list ──────────────────
        tool_messages = []
        for (tc_id, tool_name, content), post_result in zip(pending, post_results):
            final_content = (
                post_result.modified_output
                if post_result.modified_output is not None
                else content
            )
            tool_messages.append(ToolMessage(
                content=final_content,
                tool_call_id=tc_id,
                name=tool_name,
            ))

        return {"messages": tool_messages}


async def pump_notifications_node(state: AgentState) -> dict:
    """Drain WorkerPool.notification_queue into state.team_notifications.

    Runs as a graph node BEFORE agent_node so the coordinator sees
    worker results on the next LLM turn.
    """
    if not is_coordinator_mode() and not getattr(state, 'coordinator_mode', False):
        return {}
    from agent.team.tools import _POOL
    notifications = []
    while True:
        try:
            notif = _POOL.notification_queue.get_nowait()
            notifications.append(notif)
        except Exception:
            break
    if notifications:
        return {"team_notifications": notifications}
    return {}


def should_compact(state: AgentState) -> str:
    """Check if conversation needs compaction via token estimation or message count."""
    # Token-based check (primary)
    if is_context_overflow(state.messages):
        return "summarize"

    # Fallback: message count
    if len(state.messages) > config.MAX_MESSAGES_BEFORE_SUMMARY:
        return "summarize"

    return END


def build_graph(checkpointer=None):
    """
    Build and compile the LangGraph StateGraph.

    Args:
        checkpointer: Persistence checkpointer (AsyncSqliteSaver, MemorySaver, etc).

    Returns:
        Compiled graph.
    """
    # Create LLM with tools bound for both agents
    llm_planner = get_llm(PLANNER_TOOLS)
    llm_coder = get_llm(CODER_TOOLS)

    # Create the graph
    graph = StateGraph(AgentState)

    # ── Add nodes ───────────────────────────────────────────
    graph.add_node("agent", partial(agent_node, llm_planner=llm_planner, llm_coder=llm_coder))
    # Always use HookedToolNode — built-in hooks handle permissions & snapshots
    tool_node = HookedToolNode(ALL_TOOLS)
    graph.add_node("tools", tool_node)
    graph.add_node("check_compact", lambda state: state)  # passthrough for routing
    graph.add_node("summarize", summarize_node)
    # Notification pump: drains WorkerPool queue into state before each LLM turn
    graph.add_node("pump_notifications", pump_notifications_node)

    # ── Add edges ───────────────────────────────────────────
    # Start → pump_notifications → agent
    graph.set_entry_point("pump_notifications")
    graph.add_edge("pump_notifications", "agent")

    # Agent → tools (if tool calls) or check compaction
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {
            "tools": "tools",
            END: "check_compact",
        },
    )

    # Tools → pump_notifications → agent (loop back, draining new worker notifications)
    graph.add_edge("tools", "pump_notifications")

    # Check compaction → summarize or end
    graph.add_conditional_edges(
        "check_compact",
        should_compact,
        {
            "summarize": "summarize",
            END: END,
        },
    )

    # Summarize → end
    graph.add_edge("summarize", END)

    # ── Compile ─────────────────────────────────────────────
    compiled = graph.compile(checkpointer=checkpointer)

    return compiled
