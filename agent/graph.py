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
"""

from functools import partial
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from models.state import AgentState
from agent.nodes import (
    agent_node, summarize_node, get_llm,
    is_context_overflow,
)
from agent.tools.code_search import code_search, grep_search, batch_read
from agent.tools.file_ops import file_read, file_write, file_list, file_edit, glob_search
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
from agent.subagents import task_explore, task_general
import config


# ── Separate Tools for Swarm Roles ──────────────────────────
PLANNER_TOOLS = [
    code_search, grep_search, batch_read, semantic_search, index_codebase,
    file_read, glob_search, file_list, code_analyze, webfetch,
    lsp_definition, lsp_references, lsp_hover, lsp_symbols, lsp_diagnostics,
    lsp_go_to_definition, lsp_find_references, handoff_to_coder,
    reply_to_user, task_explore
]

CODER_TOOLS = PLANNER_TOOLS + [
    file_edit, file_write, terminal_exec, handoff_to_planner, task_general
]

ALL_TOOLS = list({t.name: t for t in PLANNER_TOOLS + CODER_TOOLS}.values())


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
    graph.add_node("tools", ToolNode(ALL_TOOLS))
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
