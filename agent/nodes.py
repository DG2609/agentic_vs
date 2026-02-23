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
- **Web content**: `webfetch`.
- **LSP precision**: `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_diagnostics`.
- **Semantic search**: `semantic_search`, `index_codebase`.

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
1. Always explore the codebase first.
2. PRIORITY (Documentation): Propose or write comprehensive documentation (e.g. system architecture, docstrings, README updates) BEFORE actual implementation. Well-documented projects are mandatory.
3. Formulate a step-by-step actionable plan.
4. Hand off to the Coder agent to execute the plan.
"""

CODER_PROMPT = BASE_SYSTEM_PROMPT + """
# YOUR ROLE: CODER AGENT (Expert Software Engineer)
Your job is to execute the plan provided by the Planner.
You CAN edit files and run terminal commands.

Rules:
1. Use `file_edit` (AIDER-STYLE) to modify code.
2. Use `terminal_exec` to run tests and builds. Don't stop if there's an error; read the output and fix it.
3. If the plan is fundamentally flawed or you need architectural guidance, call `handoff_to_planner`.
4. Be concise. Focus on doing the work.
"""

# ── Compaction constants ────────────────────────────────────────
COMPACTION_BUFFER = getattr(config, "COMPACTION_BUFFER", 20000)
PRUNE_MINIMUM = getattr(config, "PRUNE_MINIMUM", 20000)
PRUNE_PROTECT = getattr(config, "PRUNE_PROTECT", 40000)

# ── Doom loop detection ────────────────────────────────────────
DOOM_LOOP_MAX = 2  # max identical consecutive tool calls before breaking


# ─────────────────────────────────────────────────────────────
# LLM creation
# ─────────────────────────────────────────────────────────────
def _create_llm(streaming: bool = True, temperature: float = 0.3):
    """Create LLM instance based on configured provider."""
    if config.LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.OPENAI_MODEL,
            api_key=config.OPENAI_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )
    else:
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=config.OLLAMA_MODEL,
            base_url=config.OLLAMA_BASE_URL,
            temperature=temperature,
        )


def get_llm(tools: list):
    """Create and return the LLM with tools bound."""
    llm = _create_llm(streaming=True)
    return llm.bind_tools(tools)


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
# Doom loop detection
# ─────────────────────────────────────────────────────────────
def _detect_doom_loop(messages: list) -> bool:
    """Detect if the agent is stuck in a loop of identical tool calls."""
    recent_tool_calls = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            # serialize tool call for comparison
            calls = [(tc.get("name", ""), json.dumps(tc.get("args", {}), sort_keys=True))
                     for tc in msg.tool_calls]
            recent_tool_calls.append(tuple(calls))
        elif isinstance(msg, HumanMessage):
            break  # stop at last human message

        if len(recent_tool_calls) >= DOOM_LOOP_MAX:
            break

    if len(recent_tool_calls) >= DOOM_LOOP_MAX:
        # Check if all are identical
        if len(set(recent_tool_calls)) == 1:
            return True
    return False


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
            if tokens > 200:  # only prune large outputs
                # Keep first 100 chars as preview
                content = getattr(msg, "content", "")
                if isinstance(content, str):
                    preview = content[:200]
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

    # Doom loop detection
    if _detect_doom_loop(messages):
        return {
            "messages": [AIMessage(content=(
                "I notice I'm repeating the same tool calls. Let me step back and "
                "try a different approach. Could you clarify what you need?"
            ))]
        }

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

    response = await llm.ainvoke(full_messages)
    
    active_agent = state.active_agent
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            if tc.get("name") == "handoff_to_coder":
                active_agent = "coder"
            elif tc.get("name") == "handoff_to_planner":
                active_agent = "planner"

    return {"messages": [response], "active_agent": active_agent}


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

    llm = _create_llm(streaming=False, temperature=0.1)
    response = await llm.ainvoke([HumanMessage(content=prompt)])

    # Remove old messages, keep recent ones
    delete_messages = [RemoveMessage(id=m.id) for m in to_summarize
                       if hasattr(m, "id") and m.id]

    return {
        "summary": response.content,
        "messages": delete_messages,
    }
