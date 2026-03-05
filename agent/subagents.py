"""
Subagent tools — explore and general agents that the main build agent
can delegate work to via tool calls.

Each subagent runs its own mini LLM loop with a restricted tool set
and specialized system prompt.
"""
import logging
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

import config
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

# Max iterations per subagent invocation
SUBAGENT_MAX_STEPS = 15


# ─────────────────────────────────────────────────────────────
# Shared subagent runner
# ─────────────────────────────────────────────────────────────
async def _run_subagent(
    task: str,
    system_prompt: str,
    tools: list,
    max_steps: int = SUBAGENT_MAX_STEPS,
) -> str:
    """Run a subagent loop: LLM → tool calls → LLM → ... → final text."""
    from agent.nodes import _create_llm, _invoke_with_retry, _repair_tool_calls

    # Use fast model for subagents — they do search/exploration, not deep reasoning
    llm = _create_llm(streaming=False, temperature=0.2, fast=True)
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ]

    for step in range(max_steps):
        response = await _invoke_with_retry(llm_with_tools, messages)
        response = _repair_tool_calls(response)
        messages.append(response)

        # If no tool calls, return the text response
        if not response.tool_calls:
            return response.content or "(subagent completed with no output)"

        # Execute tool calls
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_id = tc.get("id", f"call_{step}_{tool_name}")

            # Find and invoke the tool
            matched = next((t for t in tools if t.name == tool_name), None)
            if matched:
                try:
                    result = await matched.ainvoke(tool_args)
                    # Use universal truncation — saves full output to disk if large
                    result_str = truncate_output(str(result))
                except Exception as e:
                    result_str = f"Error: {e}"
            else:
                result_str = f"Error: unknown tool '{tool_name}'"

            messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))

    return "(subagent reached max steps)"


# ─────────────────────────────────────────────────────────────
# Explore subagent — search-only, no edits
# ─────────────────────────────────────────────────────────────
EXPLORE_PROMPT = """\
You are a file search specialist. You excel at thoroughly navigating and exploring codebases.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use glob_search for broad file pattern matching
- Use grep_search for searching file contents with regex
- Use code_search for searching across multiple files
- Use file_read when you know the specific file path
- Use batch_read to read multiple files at once
- Use file_list to explore directory structure
- Use terminal_exec for file operations like listing or counting
- Adapt your search approach based on the thoroughness level in the task
- Return file paths as relative paths from the working directory
- Do NOT create or modify any files
- Be concise in your final response — summarize findings clearly

Complete the search request efficiently and report your findings clearly.
"""


def _get_explore_tools():
    """Get the restricted tool set for explore subagent."""
    from agent.tools.code_search import code_search, grep_search, batch_read
    from agent.tools.file_ops import file_read, file_list, glob_search
    from agent.tools.terminal import terminal_exec
    from agent.tools.web import webfetch
    from agent.tools.code_analyzer import code_analyze
    return [
        file_read, file_list, glob_search,
        code_search, grep_search, batch_read,
        terminal_exec, webfetch, code_analyze,
    ]


@tool
async def task_explore(task: str) -> str:
    """Delegate a search/exploration task to the explore subagent.

    The explore subagent is a fast, search-only specialist. It can find files,
    search code, read files, and run bash commands, but CANNOT edit or write files.

    Use this tool when you need to:
    - Explore a codebase to understand its structure
    - Find files by patterns (e.g. "find all Python files in src/")
    - Search code for keywords or patterns
    - Answer questions about the codebase
    - Gather context before making changes

    When calling, specify the thoroughness level:
    - "quick" for basic searches
    - "medium" for moderate exploration
    - "very thorough" for comprehensive analysis

    Args:
        task: Description of what to search for, including desired thoroughness.

    Returns:
        Search results and findings from the explore subagent.
    """
    logger.info(f"[explore] Starting task: {task[:100]}...")
    tools = _get_explore_tools()
    result = await _run_subagent(task, EXPLORE_PROMPT, tools)
    logger.info(f"[explore] Completed. Result length: {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# General subagent — multi-purpose
# ─────────────────────────────────────────────────────────────
GENERAL_PROMPT = """\
You are a general-purpose software engineering agent. You can research, analyze, \
and execute multi-step tasks independently.

Guidelines:
- Read files before editing them
- Use code_search and grep_search to find relevant code
- Use file_edit for surgical changes, file_write for new files
- Use terminal_exec for builds, tests, git commands
- Be thorough but efficient — minimize unnecessary tool calls
- Report your findings and actions concisely
- Follow existing code conventions in the project
"""


def _get_general_tools():
    """Get the tool set for general subagent (all except LSP)."""
    from agent.tools.code_search import code_search, grep_search, batch_read
    from agent.tools.file_ops import file_read, file_write, file_list, file_edit, glob_search
    from agent.tools.terminal import terminal_exec
    from agent.tools.web import webfetch
    from agent.tools.code_analyzer import code_analyze
    return [
        file_read, file_write, file_list, file_edit, glob_search,
        code_search, grep_search, batch_read,
        terminal_exec, webfetch, code_analyze,
    ]


@tool
async def task_explore_parallel(tasks: list) -> str:
    """Run multiple independent exploration tasks in parallel (faster than sequential).

    Each task runs as a separate explore subagent. All tasks execute concurrently
    using asyncio.gather, so N tasks take ~1x time instead of Nx.

    Use this when you need to explore several unrelated files or answer multiple
    independent questions at once.

    Args:
        tasks: List of task descriptions (strings). Each becomes an independent
               explore subagent. Maximum 5 tasks.

    Returns:
        Combined results from all tasks, each labeled with its index.
    """
    import asyncio

    if not tasks:
        return "Error: no tasks provided."

    # Normalize — may arrive as plain strings or dicts
    task_strs = []
    for t in tasks[:5]:  # cap at 5
        if isinstance(t, str):
            task_strs.append(t)
        elif isinstance(t, dict):
            task_strs.append(t.get("task", str(t)))
        else:
            task_strs.append(str(t))

    logger.info(f"[explore_parallel] Starting {len(task_strs)} tasks concurrently")

    tools = _get_explore_tools()

    async def run_one(idx: int, task: str) -> str:
        result = await _run_subagent(task, EXPLORE_PROMPT, tools)
        return f"=== Task {idx + 1}: {task[:60]}{'...' if len(task) > 60 else ''} ===\n{result}"

    results = await asyncio.gather(
        *[run_one(i, t) for i, t in enumerate(task_strs)],
        return_exceptions=True,
    )

    parts = []
    for r in results:
        if isinstance(r, Exception):
            parts.append(f"Error: {r}")
        else:
            parts.append(str(r))

    logger.info(f"[explore_parallel] All {len(task_strs)} tasks completed")
    return "\n\n".join(parts)


@tool
async def task_general(task: str) -> str:
    """Delegate a multi-step task to the general-purpose subagent.

    The general subagent can research, analyze, read, write, and edit files.
    It operates independently and returns results when done.

    Use this tool when you need to:
    - Execute a discrete unit of work in parallel
    - Research a complex question that requires multiple steps
    - Perform a self-contained task (e.g. "add error handling to all API routes")
    - Break down and execute a sub-task independently

    The general subagent has access to file read/write/edit, search, terminal,
    and web tools, but NOT LSP tools.

    Args:
        task: Detailed description of the task to complete.

    Returns:
        Results and summary of actions taken by the general subagent.
    """
    logger.info(f"[general] Starting task: {task[:100]}...")
    tools = _get_general_tools()
    result = await _run_subagent(task, GENERAL_PROMPT, tools)
    logger.info(f"[general] Completed. Result length: {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# Reviewer subagent — validates Coder's work
# ─────────────────────────────────────────────────────────────

REVIEWER_PROMPT = """\
You are a senior code reviewer. Your job is to validate recent changes and report issues.

You have access to LSP diagnostics, test runner, code quality analysis, and file reading tools.

Review workflow:
1. Read the changed files (provided in the task)
2. Run `lsp_diagnostics` on each changed file — report any errors or warnings
3. Run `run_tests` to check test suite status
4. Run `code_quality` on heavily changed files — flag new complexity issues
5. Check for: missing error handling, hardcoded values, missing type hints (Python)
6. Summarize findings as:
   - PASSED ✅ or FAILED ❌ overall verdict
   - List of specific issues by severity (error/warning/suggestion)
   - Which files/lines need attention

Be precise. Only report real issues — don't nitpick style unless it's a clear problem.
"""


def _get_reviewer_tools():
    """Tools for the reviewer — read-only + diagnostics + tests."""
    from agent.tools.code_search import code_search, grep_search, batch_read
    from agent.tools.file_ops import file_read, glob_search
    from agent.tools.lsp import lsp_diagnostics, lsp_symbols
    from agent.tools.code_quality import code_quality
    from agent.tools.test_runner import run_tests
    return [
        file_read, glob_search,
        code_search, grep_search, batch_read,
        lsp_diagnostics, lsp_symbols,
        code_quality, run_tests,
    ]


@tool
async def task_review(task: str) -> str:
    """Delegate code review to the Reviewer subagent.

    The Reviewer runs LSP diagnostics, tests, and code quality checks on
    recently changed files. It returns a PASSED/FAILED verdict with a list
    of specific issues.

    Use this after the Coder agent finishes implementing a feature or fix,
    to validate correctness before committing.

    Args:
        task: Description of what was changed and which files to review.
              Example: "Review changes to agent/nodes.py and agent/tools/git.py"

    Returns:
        Review verdict (PASSED/FAILED) with list of issues found.
    """
    logger.info(f"[reviewer] Starting review: {task[:100]}...")
    tools = _get_reviewer_tools()
    result = await _run_subagent(task, REVIEWER_PROMPT, tools)
    logger.info(f"[reviewer] Review complete. Result length: {len(result)}")
    return result
