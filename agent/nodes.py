"""
Agent node functions for the LangGraph StateGraph.
Supports both Ollama (self-hosted) and OpenAI providers.

Upgraded with:
- Token-based compaction (replaces naive message count)
- Structured compaction template (Goal/Discoveries/Accomplished/Files)
- Prune old tool outputs before compaction
- AGENTS.md / project rules injection
- Doom loop detection
"""
import os
import json
from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    RemoveMessage,
)
from models.state import AgentState
import config
from agent.tools.truncation import estimate_tokens

# LangGraph interrupt (human-in-the-loop). Available in LangGraph >= 0.2.57.
try:
    from langgraph.types import interrupt as _lg_interrupt
    _INTERRUPT_AVAILABLE = True
except ImportError:
    _lg_interrupt = None
    _INTERRUPT_AVAILABLE = False

BASE_SYSTEM_PROMPT = """You are an expert AI coding assistant with deep expertise in software engineering.

## Core Rules (MANDATORY)
1. **TOOL EXCLUSIVITY:** You are an automated agent. You MUST perform all actions and communications by invoking native tools. DO NOT write raw markdown JSON tool calls, use the actual native tool-calling format.
2. **REPLYING & CHATTING:** If you want to say "hi", greet the user, explain your actions, or answer a question, you MUST call the `reply_to_user` tool. Provide your conversational response inside the `message` parameter. Do not output raw text as a substitute for this tool.
3. **ACT, DON'T ASK:** When asked to perform a task (e.g., "Refactor X" or "Fix Y"), use appropriate codebase tools immediately. Do not ask for permission.
4. **ALWAYS ANALYZE RESULTS:** After a tool runs, use `reply_to_user` to synthesize the findings for the user. Do not dump raw tool output.
5. **READ BEFORE EDIT:** Always use `file_read` BEFORE `file_edit` or `file_write` to understand current state.
6. **WHEN A TOOL FAILS, ADAPT:** Try a different approach or tool. Don't blindly repeat failing calls.

## Tool Usage Patterns
- **Find code**: `code_search` or `grep_search`.
- **Read files**: `file_read` or `batch_read`.
- **Edit files**: `file_edit` uses AIDER-STYLE SEARCH/REPLACE blocks. `old_string` acts as the SEARCH block (must uniquely identify the section even with slightly wrong indentation), and `new_string` acts as the REPLACE block.
- **Explore structure**: `file_list`, `code_analyze`.
- **Run commands**: `terminal_exec`.
- **Web content**: `webfetch`, `web_search`.
- **LSP precision**: `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_diagnostics`.
- **Semantic search**: `semantic_search`, `index_codebase`.
- **Git workflow**: `git_status` → understand state → `git_diff` → review changes → `git_add` → `git_commit`.
  Always check `git_status` before committing. Use `git_log` to understand project history. Use `git_blame` before editing old code.
- **Agent Skills**: `skill_list` to see available workflow skills; `skill_invoke(name, arguments)` to load a skill's instructions into context (commit workflow, security audit, code review, refactoring guide, agent personas, etc.); `skill_create(name, description, content)` to save a new workflow skill.

## Parallel Tool Calls (IMPORTANT)
When you need multiple independent pieces of information, invoke multiple tools **simultaneously in a single response**. Do NOT chain them sequentially if they don't depend on each other.

- **Good**: Reading files A, B, C → call `file_read(A)` + `file_read(B)` + `file_read(C)` in one step.
- **Good**: Search + read → `code_search(...)` + `file_list(...)` together.
- **Bad**: Call `file_read(A)`, wait, then call `file_read(B)` in a separate step.

## Anti-Patterns (NEVER DO THESE)
- ❌ "Tôi sẽ sử dụng công cụ X để..." → Just call the tool silently
- ❌ "Bạn muốn tiếp tục không?" → Continue automatically
- ❌ Copying raw file_list/file_read output into your response

"""

PLANNER_PROMPT = BASE_SYSTEM_PROMPT + """
# YOUR ROLE: PLANNER AGENT (Senior Software Architect)
Your job is to read, analyze, and plan.
You CANNOT edit files or run terminal commands.
Use your tools (code_search, read, lsp, etc.) to understand the codebase.
When you have a complete understanding and a solid plan, call the `handoff_to_coder` tool with your instructions.

Rules:
1. **Start with context_build** — run `context_build(description)` first to auto-find relevant files.
2. Use parallel tool calls for multi-file reads.
3. Use `code_quality` to assess file health before planning refactors.
4. Use `dep_graph` to understand module dependencies before architectural changes.
5. Use `memory_search` at session start to recall past decisions about this project.
5. PRIORITY (Documentation): Propose comprehensive docs BEFORE implementation.
6. Formulate a clear, step-by-step actionable plan.
7. Hand off to the Coder agent to execute the plan.
"""

CODER_PROMPT = BASE_SYSTEM_PROMPT + """
# YOUR ROLE: CODER AGENT (Expert Software Engineer)
Your job is to execute the plan provided by the Planner.
You CAN edit files, run terminal commands, and make git commits.

Rules:
1. Use `file_edit` (AIDER-STYLE) for single-file changes; use `file_edit_batch` for atomic multi-file changes.
2. Use `run_tests` to verify your changes. Don't stop if there's an error — read the output and fix it.
3. Use `terminal_exec` for builds and other shell commands.
4. If the plan is fundamentally flawed or you need architectural guidance, call `handoff_to_planner`.
5. Be concise. Focus on doing the work.
6. **Git workflow**: After completing a logical unit of work, use `git_add` + `git_commit` to save progress.
   - Check `git_status` before committing to see exactly what changed.
   - Write clear commit messages describing WHAT changed and WHY.
   - Never commit broken code — run `run_tests` first.
7. Use `memory_save` to persist important facts, patterns, or decisions for future sessions.
"""

# ── Compaction constants ────────────────────────────────────────
COMPACTION_BUFFER = getattr(config, "COMPACTION_BUFFER", 20000)
PRUNE_MINIMUM = getattr(config, "PRUNE_MINIMUM", 20000)
PRUNE_PROTECT = getattr(config, "PRUNE_PROTECT", 40000)

# ── Doom loop detection ────────────────────────────────────────
DOOM_LOOP_MAX = 3  # max identical consecutive tool calls before breaking


# ─────────────────────────────────────────────────────────────
# LLM creation
# ─────────────────────────────────────────────────────────────
def _create_llm(streaming: bool = True, temperature: float = 0.3, fast: bool = False):
    """Create LLM instance based on configured provider.

    Args:
        streaming: Enable streaming output.
        temperature: Sampling temperature.
        fast: If True, use the cheaper/faster model (for subagents, summarization).
              Falls back to the main model if no fast model is configured.
    """
    if config.LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        model = (getattr(config, "OPENAI_FAST_MODEL", "") or config.OPENAI_MODEL
                 if fast else config.OPENAI_MODEL)
        return ChatOpenAI(
            model=model,
            api_key=config.OPENAI_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )
    else:
        from langchain_ollama import ChatOllama
        model = (getattr(config, "OLLAMA_FAST_MODEL", "") or config.OLLAMA_MODEL
                 if fast else config.OLLAMA_MODEL)
        return ChatOllama(
            model=model,
            base_url=config.OLLAMA_BASE_URL,
            temperature=temperature,
        )


def get_llm(tools: list):
    """Create and return the LLM with tools bound."""
    llm = _create_llm(streaming=True)
    return llm.bind_tools(tools)


# ─────────────────────────────────────────────────────────────
# Retry wrapper for LLM invocation
# ─────────────────────────────────────────────────────────────
async def _invoke_with_retry(llm, messages: list, max_retries: int = 3) -> AIMessage:
    """Invoke LLM with retry logic for transient failures.

    Retries on connection errors and rate limits with exponential backoff.
    """
    import asyncio
    import traceback as _tb

    for attempt in range(max_retries):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            err_msg = str(e).lower()
            is_retryable = any(k in err_msg for k in [
                "timeout", "connection", "rate_limit", "429", "503", "502",
                "overloaded", "cudamalloc", "out of memory",
            ])
            if is_retryable and attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    f"[llm] Retryable error on attempt {attempt + 1}/{max_retries}: "
                    f"{type(e).__name__}: {e} — retrying in {wait}s"
                )
                await asyncio.sleep(wait)
                continue
            logger.error(
                f"[llm] Fatal error after {attempt + 1} attempt(s): "
                f"{type(e).__name__}: {e}\n{_tb.format_exc()}"
            )
            raise


# ─────────────────────────────────────────────────────────────
# Repair malformed tool calls from LLM
# ─────────────────────────────────────────────────────────────
def _repair_tool_calls(response: AIMessage) -> AIMessage:
    """Fix common LLM tool call issues (e.g., string args instead of dict).

    Some models output tool call arguments as a JSON string rather than
    a parsed dict. This normalizes them.
    """
    if not hasattr(response, "tool_calls") or not response.tool_calls:
        return response

    repaired = []
    for tc in response.tool_calls:
        args = tc.get("args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {"input": args}
        repaired.append({**tc, "args": args})

    response.tool_calls = repaired
    return response


# ─────────────────────────────────────────────────────────────
# Token estimation for messages
# ─────────────────────────────────────────────────────────────
def _msg_tokens(msg) -> int:
    """Estimate token count for a single message."""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return estimate_tokens(content)
    elif isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                total += estimate_tokens(json.dumps(part))
            elif isinstance(part, str):
                total += estimate_tokens(part)
        return total
    return 0


def _total_tokens(messages: list) -> int:
    """Estimate total tokens across all messages."""
    return sum(_msg_tokens(m) for m in messages)


# ─────────────────────────────────────────────────────────────
# Project rules loader (AGENTS.md, CLAUDE.md, etc.)
# ─────────────────────────────────────────────────────────────
def _load_project_rules(workspace: str) -> str:
    """Load project rules from AGENTS.md / CLAUDE.md in workspace root."""
    if not workspace:
        return ""

    rules_parts = []
    for name in getattr(config, "RULES_FILENAMES", ["AGENTS.md", "CLAUDE.md"]):
        path = os.path.join(workspace, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(10000)  # limit to 10KB
                rules_parts.append(f"## Project Rules ({name})\n{content}")
            except Exception:
                pass

    return "\n\n".join(rules_parts)


# ─────────────────────────────────────────────────────────────
# Doom loop detection + classified recovery
# ─────────────────────────────────────────────────────────────
def _get_recent_tool_calls(messages: list, n: int) -> list[tuple]:
    """Extract the last n AI tool-call tuples from the message history."""
    recent = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            calls = tuple(
                (tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True))
                for tc in msg.tool_calls
            )
            recent.append(calls)
        elif isinstance(msg, HumanMessage):
            break
        if len(recent) >= n:
            break
    return recent


def _detect_doom_loop(messages: list) -> bool:
    """Detect if the agent is stuck in a loop of identical tool calls."""
    recent = _get_recent_tool_calls(messages, DOOM_LOOP_MAX)
    if len(recent) >= DOOM_LOOP_MAX and len(set(recent)) == 1:
        return True
    return False


def _classify_doom_loop(messages: list) -> str:
    """Classify the type of doom loop for targeted recovery.

    Returns one of: 'tool_error', 'missing_file', 'search_no_result', 'unknown'
    """
    # Look at the most recent tool messages for patterns
    recent_tool_results = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            recent_tool_results.append(getattr(msg, "content", ""))
        elif isinstance(msg, HumanMessage):
            break
        if len(recent_tool_results) >= 6:
            break

    if not recent_tool_results:
        return "unknown"

    combined = "\n".join(recent_tool_results).lower()

    if any(k in combined for k in ["error:", "exception", "traceback", "failed"]):
        return "tool_error"
    if any(k in combined for k in ["not found", "does not exist", "no such file"]):
        return "missing_file"
    if any(k in combined for k in ["no matches", "no results", "0 results"]):
        return "search_no_result"
    return "unknown"


def _build_recovery_message(loop_type: str, messages: list) -> str:
    """Build a targeted recovery hint based on loop classification."""
    # Extract the repeated tool name for context
    recent = _get_recent_tool_calls(messages, DOOM_LOOP_MAX)
    tool_name = recent[0][0][0] if recent and recent[0] else "unknown tool"

    if loop_type == "tool_error":
        return (
            f"I've called `{tool_name}` {DOOM_LOOP_MAX} times and it keeps failing. "
            "Let me try a different approach: I'll check the error details and adapt."
        )
    elif loop_type == "missing_file":
        return (
            f"I've been trying `{tool_name}` but the target doesn't exist. "
            "Let me search more broadly for the correct path."
        )
    elif loop_type == "search_no_result":
        return (
            f"`{tool_name}` keeps returning no results. "
            "Let me broaden my search terms or look in different locations."
        )
    else:
        return (
            f"I'm repeating `{tool_name}` without progress. "
            "Let me reassess my approach and try something different."
        )


# ─────────────────────────────────────────────────────────────
# Overflow detection
# ─────────────────────────────────────────────────────────────
def _get_model_limit() -> int:
    """Get token limit for current model."""
    model_name = (config.OPENAI_MODEL if config.LLM_PROVIDER == "openai"
                  else config.OLLAMA_MODEL)
    return config.MODEL_CONTEXT_LIMITS.get(model_name, 32768)


def is_context_overflow(messages: list) -> bool:
    """Check if total tokens exceed safe threshold."""
    limit = _get_model_limit()
    total = _total_tokens(messages)
    return total > (limit - COMPACTION_BUFFER)


# ─────────────────────────────────────────────────────────────
# Prune old tool outputs
# ─────────────────────────────────────────────────────────────
def _prune_tool_outputs(messages: list) -> list:
    """Replace old/large tool outputs with compact placeholders.

    Keeps recent tool outputs intact (protected by PRUNE_PROTECT from tail).
    Compacts older ones that exceed PRUNE_MINIMUM total.
    """
    total = _total_tokens(messages)
    if total <= PRUNE_MINIMUM:
        return messages

    # Calculate protected zone: last PRUNE_PROTECT tokens
    tail_tokens = 0
    protect_from = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        tail_tokens += _msg_tokens(messages[i])
        if tail_tokens >= PRUNE_PROTECT:
            protect_from = i
            break

    pruned = []
    for i, msg in enumerate(messages):
        if i < protect_from and isinstance(msg, ToolMessage):
            tokens = _msg_tokens(msg)
            if tokens > 200:  # only prune large outputs (>200 tokens / ~800 chars)
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    preview = content[:800]  # preview ~200 tokens worth
                    pruned_msg = ToolMessage(
                        content=f"[Output compacted — {tokens} tokens → preview]\n{preview}...",
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", ""),
                    )
                    pruned.append(pruned_msg)
                    continue
        pruned.append(msg)

    return pruned


# ─────────────────────────────────────────────────────────────
# Agent node
# ─────────────────────────────────────────────────────────────
async def agent_node(state: AgentState, llm_planner, llm_coder) -> dict:
    """
    Main agent node — invokes LLM with current messages and tools.
    Includes: project rules injection, doom loop detection, overflow check.
    Dynamically routes to Planner or Coder based on active_agent state.
    """
    messages = list(state.messages)

    # Doom loop detection + classified recovery
    if _detect_doom_loop(messages):
        loop_type = _classify_doom_loop(messages)
        recovery_msg = _build_recovery_message(loop_type, messages)
        return {"messages": [AIMessage(content=recovery_msg)]}

    # Build system message & pick LLM
    if state.active_agent == "planner":
        system_content = PLANNER_PROMPT
        llm = llm_planner
    else:
        system_content = CODER_PROMPT
        llm = llm_coder

    # Inject project rules
    rules = _load_project_rules(state.workspace or config.WORKSPACE_DIR)
    if rules:
        system_content += f"\n\n{rules}"

    if state.summary:
        system_content += f"\n\n## Previous Conversation Summary\n{state.summary}"
    if state.workspace:
        system_content += f"\n\n## Current Workspace\n{state.workspace}"

    full_messages = [SystemMessage(content=system_content)] + _prune_tool_outputs(messages)

    response = await _invoke_with_retry(llm, full_messages)
    response = _repair_tool_calls(response)

    active_agent = state.active_agent
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            if tc.get("name") == "handoff_to_coder":
                active_agent = "coder"
            elif tc.get("name") == "handoff_to_planner":
                active_agent = "planner"

    # Track token budget
    turn_tokens = _msg_tokens(response)
    new_turns = state.session_turns + 1
    new_tokens = state.total_tokens_used + turn_tokens

    return {
        "messages": [response],
        "active_agent": active_agent,
        "session_turns": new_turns,
        "total_tokens_used": new_tokens,
    }


# ─────────────────────────────────────────────────────────────
# Compaction template
# ─────────────────────────────────────────────────────────────
COMPACTION_TEMPLATE = """\
Analyze the conversation and create a structured summary for continuing work.
Focus on preserving actionable context and decisions.

Conversation messages:
{conversation}

{previous_summary}

Generate a summary with EXACTLY this structure:

## Goal
What the user is trying to accomplish (1-2 sentences)

## Key Instructions
Specific requirements or constraints from the user (bullet list)

## Discoveries
Important facts, code patterns, file locations found during work (bullet list)

## Accomplished
What has been completed so far (bullet list with file paths)

## Active Files
Files that were read/modified with brief context (bullet list: path — description)

## Next Steps
What was about to happen or still needs to be done (bullet list)
"""


# ─────────────────────────────────────────────────────────────
# Summarize / Compact node
# ─────────────────────────────────────────────────────────────
async def summarize_node(state: AgentState) -> dict:
    """
    Smart compaction: uses token estimation + structured template.
    Falls back to message count if token estimation not available.
    """
    messages = list(state.messages)

    # Check if compaction needed
    needs_compact = is_context_overflow(messages)
    if not needs_compact:
        # Fallback: message count trigger
        if len(messages) <= config.MAX_MESSAGES_BEFORE_SUMMARY:
            return {}

    # Step 1: Prune old tool outputs first
    pruned_messages = _prune_tool_outputs(messages)

    # After pruning, if within limits, just update messages
    if not is_context_overflow(pruned_messages) and len(messages) <= config.MAX_MESSAGES_BEFORE_SUMMARY:
        # Pruning was enough — update messages without full compaction
        if len(pruned_messages) != len(messages):
            delete_msgs = [RemoveMessage(id=m.id) for m in messages if hasattr(m, "id") and m.id]
            return {"messages": delete_msgs + pruned_messages}
        return {}

    # Step 2: Full compaction via LLM
    keep_count = 6
    to_summarize = pruned_messages[:-keep_count] if len(pruned_messages) > keep_count else pruned_messages

    # Build conversation text for LLM
    conv_parts = []
    for msg in to_summarize:
        role = type(msg).__name__
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content:
            # Limit each message to 800 chars for summary input
            conv_parts.append(f"[{role}]: {content[:800]}")
        elif isinstance(content, list):
            text = " ".join(str(c) for c in content)[:800]
            conv_parts.append(f"[{role}]: {text}")

    conversation_text = "\n".join(conv_parts)
    prev_summary = f"Previous summary:\n{state.summary}" if state.summary else ""

    prompt = COMPACTION_TEMPLATE.format(
        conversation=conversation_text,
        previous_summary=prev_summary,
    )

    llm = _create_llm(streaming=False, temperature=0.1, fast=True)
    response = await llm.ainvoke([HumanMessage(content=prompt)])

    # Remove old messages, keep recent ones
    delete_messages = [RemoveMessage(id=m.id) for m in to_summarize
                       if hasattr(m, "id") and m.id]

    return {
        "summary": response.content,
        "messages": delete_messages,
    }
