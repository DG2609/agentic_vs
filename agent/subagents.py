"""
Subagent tools — explore and general agents that the main build agent
can delegate work to via tool calls.

Each subagent runs its own mini LLM loop with a restricted tool set
and specialized system prompt.
"""
import logging
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

import config

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

    llm = _create_llm(streaming=False, temperature=0.2)
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
        from langchain_core.messages import ToolMessage
        for tc in response.tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_id = tc.get("id", f"call_{step}_{tool_name}")

            # Find and invoke the tool
            matched = next((t for t in tools if t.name == tool_name), None)
            if matched:
                try:
                    result = await matched.ainvoke(tool_args)
                    result_str = str(result)
                    # Truncate large outputs
                    if len(result_str) > 8000:
                        result_str = result_str[:8000] + f"\n... (truncated, {len(result_str)} total chars)"
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
