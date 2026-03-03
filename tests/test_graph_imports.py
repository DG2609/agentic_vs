"""
Smoke tests: verify the full graph + all tools import correctly.
These are fast import-time checks — no LLM calls or I/O.
"""
import pytest


def test_all_tools_importable():
    """55 tools should load without errors."""
    from agent.graph import ALL_TOOLS
    assert len(ALL_TOOLS) >= 50, f"Expected 50+ tools, got {len(ALL_TOOLS)}"


def test_planner_coder_tool_split():
    from agent.graph import PLANNER_TOOLS, CODER_TOOLS

    planner_names = {t.name for t in PLANNER_TOOLS}
    coder_names = {t.name for t in CODER_TOOLS}

    # Planner should NOT have write tools
    assert "file_write" not in planner_names
    assert "file_edit" not in planner_names
    assert "terminal_exec" not in planner_names
    assert "git_push" not in planner_names

    # Coder should have everything Planner has PLUS write tools
    assert "file_edit" in coder_names
    assert "file_edit_batch" in coder_names
    assert "run_tests" in coder_names
    assert "git_push" in coder_names
    assert "git_pull" in coder_names
    assert "task_review" in coder_names

    # Planner-only tools
    assert "handoff_to_coder" in planner_names
    assert "context_build" in planner_names
    assert "question" in planner_names


def test_no_duplicate_tool_names():
    from agent.graph import ALL_TOOLS

    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"


def test_all_required_tools_present():
    from agent.graph import ALL_TOOLS

    tool_names = {t.name for t in ALL_TOOLS}
    required = {
        # File ops
        "file_read", "file_write", "file_edit", "file_edit_batch", "file_list", "glob_search",
        # Search
        "code_search", "grep_search", "batch_read",
        # Semantic
        "semantic_search", "index_codebase",
        # LSP
        "lsp_definition", "lsp_references", "lsp_hover", "lsp_symbols", "lsp_diagnostics",
        # Git read
        "git_status", "git_diff", "git_log", "git_show", "git_blame",
        # Git write
        "git_add", "git_commit", "git_branch", "git_stash",
        # Git remote
        "git_push", "git_pull", "git_fetch", "git_merge",
        # Analysis
        "code_quality", "dep_graph", "context_build", "code_analyze",
        # Testing
        "run_tests",
        # Memory
        "memory_save", "memory_search", "memory_list", "memory_delete",
        # Subagents
        "task_explore", "task_explore_parallel", "task_general", "task_review",
        # Web
        "webfetch", "web_search",
        # Communication
        "reply_to_user", "question", "handoff_to_coder", "handoff_to_planner",
        # Planning
        "todo_read", "todo_write", "plan_enter", "plan_exit",
        # Terminal
        "terminal_exec",
    }
    missing = required - tool_names
    assert not missing, f"Missing tools: {missing}"


def test_nodes_importable():
    from agent.nodes import agent_node, summarize_node, get_llm, is_context_overflow
    assert callable(agent_node)
    assert callable(summarize_node)
    assert callable(get_llm)
    assert callable(is_context_overflow)


def test_lgs_interrupt_available():
    """LangGraph interrupt() should be available (needed for question tool)."""
    try:
        from langgraph.types import interrupt, Command
        assert callable(interrupt)
    except ImportError:
        pytest.skip("LangGraph version too old — interrupt not available")


def test_config_exports():
    import config
    for attr in ["LLM_PROVIDER", "WORKSPACE_DIR", "HOST", "PORT",
                 "TOOL_TIMEOUT", "MAX_OUTPUT_LINES", "VECTOR_BACKEND",
                 "API_KEY", "AGENT_TIMEOUT"]:
        assert hasattr(config, attr), f"config.{attr} missing"


def test_agent_state_fields():
    from models.state import AgentState
    import inspect
    fields = AgentState.model_fields
    assert "messages" in fields
    assert "summary" in fields
    assert "workspace" in fields
    assert "active_agent" in fields
    assert "session_turns" in fields
    assert "total_tokens_used" in fields
