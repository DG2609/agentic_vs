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
from agent.tools.skills import skill_invoke, skill_list, skill_create
from agent.skill_loader import load_skills as _load_skills
from agent.hooks import (
    run_pre_hooks, run_post_hooks,
    load_hooks_from_file, PRE_TOOL_HOOKS, POST_TOOL_HOOKS,
)
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
    skill_invoke, skill_list, skill_create,
]
_planner_skills, _coder_skills = _load_skills(
    existing_names={t.name for t in _CORE_TOOLS}
)


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
    # Skill system (markdown workflow skills)
    skill_invoke, skill_list,
    # External skills (read/both access)
    *_planner_skills,
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
    # Skill system (create new workflow skills)
    skill_create,
    # External skills (write access)
    *_coder_skills,
]

ALL_TOOLS = list({t.name: t for t in PLANNER_TOOLS + CODER_TOOLS}.values())


# ── Load hooks from config ───────────────────────────────────
if config.HOOKS_FILE:
    load_hooks_from_file(config.HOOKS_FILE)


class HookedToolNode:
    """Wraps LangGraph ToolNode with pre/post hook support.

    - Before each tool call: runs matching pre-hooks (can block or modify args)
    - After each tool call: runs matching post-hooks (can modify output)
    """

    def __init__(self, tools: list):
        self._inner = ToolNode(tools)
        self._has_hooks = bool(PRE_TOOL_HOOKS or POST_TOOL_HOOKS)

    async def __call__(self, state):
        if not self._has_hooks:
            return await self._inner.ainvoke(state)

        # Process tool calls with hooks
        messages = list(state["messages"]) if isinstance(state, dict) else list(state.messages)
        last_msg = messages[-1] if messages else None

        if not last_msg or not getattr(last_msg, "tool_calls", None):
            return await self._inner.ainvoke(state)

        # Run pre-hooks — check for blocks
        for tc in last_msg.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            pre_result = await run_pre_hooks(tool_name, tool_args)

            if pre_result.block:
                # Return a blocked message instead of executing
                blocked_msg = ToolMessage(
                    content=f"[BLOCKED by hook] {pre_result.reason}",
                    tool_call_id=tc.get("id", ""),
                    name=tool_name,
                )
                return {"messages": [blocked_msg]}

            # Modify args if hook requested
            if pre_result.modified_args is not None:
                tc["args"] = pre_result.modified_args

        # Execute tools normally
        result = await self._inner.ainvoke(state)

        # Run post-hooks
        result_messages = result.get("messages", []) if isinstance(result, dict) else []
        modified_messages = []
        for msg in result_messages:
            if isinstance(msg, ToolMessage):
                tool_name = getattr(msg, "name", "")
                post_result = await run_post_hooks(tool_name, {}, getattr(msg, "content", ""))
                if post_result.modified_output is not None:
                    msg = ToolMessage(
                        content=post_result.modified_output,
                        tool_call_id=msg.tool_call_id,
                        name=tool_name,
                    )
            modified_messages.append(msg)

        if modified_messages:
            result["messages"] = modified_messages

        return result


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
    tool_node = HookedToolNode(ALL_TOOLS) if (PRE_TOOL_HOOKS or POST_TOOL_HOOKS) else ToolNode(ALL_TOOLS)
    graph.add_node("tools", tool_node)
    graph.add_node("check_compact", lambda state: state)  # passthrough for routing
    graph.add_node("summarize", summarize_node)

    # ── Add edges ───────────────────────────────────────────
    # Start → agent
    graph.set_entry_point("agent")

    # Agent → tools (if tool calls) or check compaction
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {
            "tools": "tools",
            END: "check_compact",
        },
    )

    # Tools → agent (loop back)
    graph.add_edge("tools", "agent")

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
