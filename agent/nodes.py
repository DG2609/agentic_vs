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
import logging
import random
import subprocess as _subprocess
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
from agent.team.coordinator import is_coordinator_mode, get_coordinator_system_prompt
from agent.rules_loader import load_project_rules
from agent.tools.cost_tracker import cost_from_response

logger = logging.getLogger(__name__)

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
- ❌ Narrating what you're about to do instead of doing it → Call the tool silently
- ❌ Asking "do you want to continue?" → Continue automatically
- ❌ Copying raw file_list/file_read output into your response

## Collaboration
- If you notice the user's request is based on a misconception, or you spot a bug adjacent to what they asked about, flag it. You are a collaborator, not just an executor — users benefit from your judgment, not just your compliance.
- If an approach fails, diagnose why before switching tactics. Don't retry the identical action blindly, and don't abandon a viable approach after a single failure.

## Faithful Reporting
- Report outcomes faithfully. If tests fail, say so with the relevant output. If you did not run a verification step, say that rather than implying it succeeded.
- Never claim "all tests pass" when output shows failures. Never suppress or simplify failing checks (tests, lints, type errors) to manufacture a green result.
- Equally, when a check did pass or a task is complete, state it plainly. Do not hedge confirmed results with disclaimers, or re-verify things you already checked.

## Communicating with the User
Before your first tool call each turn, briefly state what you're about to do (one sentence). While working, give short updates at key moments: when you find a root cause, when you change direction, when you make meaningful progress without an intermediate update.

Write user-facing text in prose — complete sentences, not bullet fragments. Lead with the action or finding (inverted pyramid). Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless the task requires more detail. Do not summarize what you just did at the end of a response.

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
5. Focus on doing the work. Fix root causes, not symptoms.
6. **Git workflow**: After completing a logical unit of work, use `git_add` + `git_commit` to save progress.
   - Check `git_status` before committing to see exactly what changed.
   - Write clear commit messages describing WHAT changed and WHY.
   - Never commit broken code — run `run_tests` first.
7. Use `memory_save` to persist important facts, patterns, or decisions for future sessions.

## Code Comments
- Default to writing no comments. Only add one when the WHY is non-obvious: a hidden constraint, a subtle invariant, a workaround for a specific bug, behavior that would surprise a reader.
- Do not explain WHAT the code does — well-named identifiers already do that. Do not reference the current task, PR, or callers in comments; those belong in the commit message and rot as the codebase evolves.
- Do not remove existing comments unless you're removing the code they describe or you can confirm they're wrong. A comment that looks pointless may encode a constraint from a past bug not visible in the current diff.

## Verification
- Before reporting a task complete, verify it actually works: run the test, execute the script, check the output. Minimum scope means no gold-plating, not skipping verification.
- If you cannot verify (no test exists, cannot run the code), say so explicitly rather than claiming success.
"""

# ── Compaction constants (tuned to CC's exact values) ───────────
# CC: effective_window = model_context - 20K; trigger at effective - 13K
# So COMPACTION_BUFFER = 13K means we compact when 13K tokens from the edge.
# Prior value was 20K which triggered too early and wasted context.
COMPACTION_BUFFER = getattr(config, "COMPACTION_BUFFER", 13_000)
PRUNE_MINIMUM = getattr(config, "PRUNE_MINIMUM", 20_000)
PRUNE_PROTECT = getattr(config, "PRUNE_PROTECT", 40_000)
# CC's MANUAL_COMPACT_BUFFER_TOKENS = 3K; floor for manual compaction
MANUAL_COMPACT_BUFFER = 3_000

# ── Doom loop detection ────────────────────────────────────────
DOOM_LOOP_MAX = 3  # max identical consecutive tool calls before breaking

# ── Prompt cache break tracking ────────────────────────────────
_prompt_cache_breaks: int = 0
_last_cache_break_tokens: int = 0

# ── Session memory extraction ──────────────────────────────────
_SESSION_MEMORY_INTERVAL = int(os.environ.get("SHADOWDEV_SESSION_MEMORY_INTERVAL", "20"))
_session_memory_turn: int = 0  # module-level counter — turns since last extraction


async def _extract_session_memory(messages: list, workspace: str) -> None:
    """Background extraction of session memory — runs as fire-and-forget."""
    import asyncio
    from pathlib import Path
    from langchain_core.messages import HumanMessage as _HM, SystemMessage as _SM

    try:
        recent = messages[-40:]
        conversation = "\n".join(
            f"{m.__class__.__name__}: {str(getattr(m, 'content', ''))[:500]}"
            for m in recent
            if hasattr(m, "content") and getattr(m, "content", "")
        )

        extraction_prompt = (
            "Extract key information from this conversation excerpt into a concise session memory.\n\n"
            "Format:\n"
            "## Key Discoveries\n- ...\n\n"
            "## Files Changed\n- ...\n\n"
            "## Decisions Made\n- ...\n\n"
            "## Current Focus\n...\n\n"
            f"Conversation:\n{conversation[:8000]}"
        )

        # Use fast LLM directly — no tool loop needed
        llm = _create_llm(streaming=False, temperature=0.1, fast=True)
        response = await asyncio.wait_for(
            llm.ainvoke([_SM(content="You are a concise technical summarizer."), _HM(content=extraction_prompt)]),
            timeout=30.0,
        )
        summary = response.content if hasattr(response, "content") else str(response)

        mem_dir = Path(workspace or ".") / ".shadowdev"
        mem_dir.mkdir(exist_ok=True)
        (mem_dir / "session-memory.md").write_text(
            f"# Session Memory\n_Updated automatically_\n\n{summary}",
            encoding="utf-8",
        )
        logger.info("Session memory extracted to .shadowdev/session-memory.md")
    except Exception as e:
        logger.debug("Session memory extraction failed: %s", e)


def get_cache_stats() -> dict:
    """Return prompt cache break statistics for the current session."""
    return {
        "cache_breaks": _prompt_cache_breaks,
    }


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
    provider = config.LLM_PROVIDER

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        model = (config.OPENAI_FAST_MODEL or config.OPENAI_MODEL) if fast else config.OPENAI_MODEL
        openai_kwargs: dict = {}
        effort = getattr(config, "REASONING_EFFORT", "none")
        if effort != "none":
            # o1/o3 series: pass reasoning_effort via model_kwargs
            openai_kwargs["model_kwargs"] = {"reasoning_effort": effort}
        return ChatOpenAI(
            model=model,
            api_key=config.OPENAI_API_KEY,
            temperature=temperature,
            streaming=streaming,
            **openai_kwargs,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        model = (config.ANTHROPIC_FAST_MODEL or config.ANTHROPIC_MODEL) if fast else config.ANTHROPIC_MODEL
        anthropic_kwargs: dict = {}
        effort = getattr(config, "REASONING_EFFORT", "none")
        if effort != "none":
            # Claude extended thinking: map effort level to budget_tokens
            _budget_map = {"low": 1024, "medium": 4096, "high": 16384}
            budget = _budget_map.get(effort, 4096)
            anthropic_kwargs["model_kwargs"] = {
                "thinking": {"type": "enabled", "budget_tokens": budget}
            }
        return ChatAnthropic(
            model=model,
            api_key=config.ANTHROPIC_API_KEY,
            temperature=temperature,
            streaming=streaming,
            **anthropic_kwargs,
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        model = (config.GOOGLE_FAST_MODEL or config.GOOGLE_MODEL) if fast else config.GOOGLE_MODEL
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=config.GOOGLE_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "groq":
        from langchain_groq import ChatGroq
        model = (config.GROQ_FAST_MODEL or config.GROQ_MODEL) if fast else config.GROQ_MODEL
        return ChatGroq(
            model=model,
            api_key=config.GROQ_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "azure":
        from langchain_openai import AzureChatOpenAI
        model = (config.AZURE_OPENAI_FAST_MODEL or config.AZURE_OPENAI_MODEL) if fast else config.AZURE_OPENAI_MODEL
        return AzureChatOpenAI(
            azure_deployment=model,
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_API_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "vllm":
        from langchain_openai import ChatOpenAI
        model = (config.VLLM_FAST_MODEL or config.VLLM_MODEL) if fast else config.VLLM_MODEL
        if not model:
            model = "default"
        return ChatOpenAI(
            model=model,
            openai_api_key=config.VLLM_API_KEY,
            openai_api_base=config.VLLM_BASE_URL,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "llamacpp":
        from langchain_openai import ChatOpenAI
        model = (config.LLAMACPP_FAST_MODEL or config.LLAMACPP_MODEL) if fast else config.LLAMACPP_MODEL
        if not model:
            model = "local-model"
        return ChatOpenAI(
            model=model,
            openai_api_key=config.LLAMACPP_API_KEY,
            openai_api_base=config.LLAMACPP_BASE_URL,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        model = (config.LMSTUDIO_FAST_MODEL or config.LMSTUDIO_MODEL) if fast else config.LMSTUDIO_MODEL
        if not model:
            model = "local-model"
        return ChatOpenAI(
            model=model,
            openai_api_key=config.LMSTUDIO_API_KEY,
            openai_api_base=config.LMSTUDIO_BASE_URL,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "openai_compatible":
        from langchain_openai import ChatOpenAI
        model = (config.OPENAI_COMPATIBLE_FAST_MODEL or config.OPENAI_COMPATIBLE_MODEL) if fast else config.OPENAI_COMPATIBLE_MODEL
        if not model:
            model = "default"
        return ChatOpenAI(
            model=model,
            openai_api_key=config.OPENAI_COMPATIBLE_API_KEY,
            openai_api_base=config.OPENAI_COMPATIBLE_BASE_URL,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "vertex_ai":
        from langchain_google_vertexai import ChatVertexAI
        model = (config.VERTEX_AI_FAST_MODEL or config.VERTEX_AI_MODEL) if fast else config.VERTEX_AI_MODEL
        kwargs: dict = dict(model_name=model, location=config.VERTEX_AI_LOCATION,
                            temperature=temperature, streaming=streaming)
        if config.VERTEX_AI_PROJECT:
            kwargs["project"] = config.VERTEX_AI_PROJECT
        return ChatVertexAI(**kwargs)
    elif provider == "github_copilot":
        from langchain_openai import ChatOpenAI
        model = (config.GITHUB_COPILOT_FAST_MODEL or config.GITHUB_COPILOT_MODEL) if fast else config.GITHUB_COPILOT_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.GITHUB_COPILOT_API_KEY,
            openai_api_base="https://api.githubcopilot.com",
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "aws_bedrock":
        from langchain_aws import ChatBedrock
        model = (config.BEDROCK_FAST_MODEL or config.BEDROCK_MODEL) if fast else config.BEDROCK_MODEL
        bedrock_kwargs: dict = dict(
            model_id=model,
            region_name=config.AWS_REGION,
            streaming=streaming,
            model_kwargs={"temperature": temperature},
        )
        if config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY:
            bedrock_kwargs["credentials_profile_name"] = None
            import boto3
            bedrock_kwargs["client"] = boto3.client(
                "bedrock-runtime",
                region_name=config.AWS_REGION,
                aws_access_key_id=config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            )
        return ChatBedrock(**bedrock_kwargs)
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI
        model = (config.MISTRAL_FAST_MODEL or config.MISTRAL_MODEL) if fast else config.MISTRAL_MODEL
        return ChatMistralAI(
            model=model,
            mistral_api_key=config.MISTRAL_API_KEY,
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "together":
        from langchain_openai import ChatOpenAI
        model = (config.TOGETHER_FAST_MODEL or config.TOGETHER_MODEL) if fast else config.TOGETHER_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.TOGETHER_API_KEY,
            openai_api_base="https://api.together.xyz/v1",
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "fireworks":
        from langchain_openai import ChatOpenAI
        model = (config.FIREWORKS_FAST_MODEL or config.FIREWORKS_MODEL) if fast else config.FIREWORKS_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.FIREWORKS_API_KEY,
            openai_api_base="https://api.fireworks.ai/inference/v1",
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "deepseek":
        from langchain_openai import ChatOpenAI
        model = (config.DEEPSEEK_FAST_MODEL or config.DEEPSEEK_MODEL) if fast else config.DEEPSEEK_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.DEEPSEEK_API_KEY,
            openai_api_base="https://api.deepseek.com/v1",
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "perplexity":
        from langchain_openai import ChatOpenAI
        model = (config.PERPLEXITY_FAST_MODEL or config.PERPLEXITY_MODEL) if fast else config.PERPLEXITY_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.PERPLEXITY_API_KEY,
            openai_api_base="https://api.perplexity.ai",
            temperature=temperature,
            streaming=streaming,
        )
    elif provider == "xai":
        from langchain_openai import ChatOpenAI
        model = (config.XAI_FAST_MODEL or config.XAI_MODEL) if fast else config.XAI_MODEL
        return ChatOpenAI(
            model=model,
            openai_api_key=config.XAI_API_KEY,
            openai_api_base="https://api.x.ai/v1",
            temperature=temperature,
            streaming=streaming,
        )
    else:  # ollama (default)
        from langchain_ollama import ChatOllama
        model = (config.OLLAMA_FAST_MODEL or config.OLLAMA_MODEL) if fast else config.OLLAMA_MODEL
        return ChatOllama(
            model=model,
            base_url=config.OLLAMA_BASE_URL,
            temperature=temperature,
        )


def get_llm(tools: list):
    """Create and return the LLM with tools bound.

    For Anthropic, passes cache_control to enable prompt caching on the tool
    list (up to 90% cost reduction on cached tokens for large tool schemas).
    """
    llm = _create_llm(streaming=True)
    if config.LLM_PROVIDER == "anthropic":
        return llm.bind_tools(tools, cache_control={"type": "ephemeral"})
    return llm.bind_tools(tools)


# ─────────────────────────────────────────────────────────────
# Reactive compaction — context overflow error detection
# ─────────────────────────────────────────────────────────────

_CONTEXT_OVERFLOW_ERRORS = (
    "context_length_exceeded",
    "prompt_too_long",
    "maximum context length",
    "context window",
    "too many tokens",
    "token limit",
)


class ContextOverflowError(Exception):
    """Raised when the LLM API rejects a request due to context window overflow.

    Caught in agent_node to trigger reactive compaction rather than surfacing
    the raw API error to the user (mirrors CC's reactive compact pattern).
    """
    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


def _is_context_overflow(exc: Exception) -> bool:
    """Return True if *exc* indicates a context-window overflow error."""
    msg = str(exc).lower()
    return any(pattern in msg for pattern in _CONTEXT_OVERFLOW_ERRORS)


class FreeUsageLimitError(Exception):
    """Raised when the API free tier limit is exhausted (not a transient error)."""
    pass


_FREE_LIMIT_PATTERNS = (
    "free tier", "free plan", "free limit", "exceeded your free",
    "upgrade your plan", "free quota", "no free",
)


def _is_free_limit_error(exc: Exception) -> bool:
    """Return True if *exc* indicates a free-tier exhaustion (non-retryable)."""
    msg = str(exc).lower()
    return any(p in msg for p in _FREE_LIMIT_PATTERNS)


def _get_retry_after(exc: Exception) -> "float | None":
    """Extract Retry-After seconds from exception headers if available."""
    resp = getattr(exc, 'response', None)
    if resp is None:
        return None
    headers = getattr(resp, 'headers', {})
    # Retry-After can be seconds or HTTP date
    ra = headers.get('retry-after') or headers.get('Retry-After')
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    # OpenAI-specific: x-ratelimit-reset-requests (in seconds like "1.5s" or "500ms")
    rr = headers.get('x-ratelimit-reset-requests', '')
    if rr.endswith('ms'):
        try:
            return float(rr[:-2]) / 1000
        except ValueError:
            pass
    if rr.endswith('s'):
        try:
            return float(rr[:-1])
        except ValueError:
            pass
    return None


# ─────────────────────────────────────────────────────────────
# Retry wrapper for LLM invocation
# ─────────────────────────────────────────────────────────────
_RETRY_BASE_MS = 500          # 500 ms starting delay (matches CC BASE_DELAY_MS)
_RETRY_MAX_MS = 32_000        # 32 s cap (matches CC max backoff)
_RETRY_MAX_DEFAULT = 10       # default max attempts
_RETRY_MAX_529 = 3            # overloaded responses get fewer retries (CC MAX_529_RETRIES)

async def _invoke_with_retry(llm, messages: list, max_retries: int = _RETRY_MAX_DEFAULT) -> AIMessage:
    """Invoke LLM with retry + exponential backoff matching CC's exact strategy.

    - Base delay: 500 ms
    - Multiplier: 2^(attempt) — so 500 ms, 1 s, 2 s, 4 s … capped at 32 s
    - 529 Overloaded: max 3 retries regardless of max_retries
    - 429 Rate-limit, 503, 502, timeout, connection: up to max_retries
    """
    import asyncio
    import traceback as _tb

    overload_count = 0

    for attempt in range(max_retries):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            err_msg = str(e).lower()

            # Context overflow is non-retryable at this level — bubble up so
            # agent_node can compact and retry the full turn instead.
            if _is_context_overflow(e):
                raise ContextOverflowError(e) from e

            # Free-tier exhaustion is non-retryable — retrying will never succeed.
            if _is_free_limit_error(e):
                raise FreeUsageLimitError(str(e)) from e

            is_529 = "529" in err_msg or "overloaded" in err_msg
            is_retryable = is_529 or any(k in err_msg for k in [
                "timeout", "connection", "rate_limit", "429", "503", "502",
                "econnreset", "econnrefused", "cudamalloc", "out of memory",
            ])

            if is_529:
                overload_count += 1
                if overload_count >= _RETRY_MAX_529:
                    logger.error(f"[llm] 529 overloaded — exceeded {_RETRY_MAX_529} overload retries, raising")
                    raise

            if is_retryable and attempt < max_retries - 1:
                wait_ms = min(_RETRY_BASE_MS * (2 ** attempt), _RETRY_MAX_MS)
                wait_ms += random.uniform(0, 0.25 * wait_ms)
                # Honour Retry-After / x-ratelimit-reset-requests from response headers
                retry_after = _get_retry_after(e)
                if retry_after is not None:
                    wait_ms = min(retry_after * 1000, 60_000)  # cap at 60 s
                wait_s = wait_ms / 1000
                logger.warning(
                    f"[llm] Retryable error attempt {attempt + 1}/{max_retries} "
                    f"({'529-overload' if is_529 else 'transient'}): "
                    f"{type(e).__name__} — retrying in {wait_s:.1f}s"
                )
                await asyncio.sleep(wait_s)
                continue

            logger.error(
                f"[llm] Fatal after {attempt + 1} attempt(s): "
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
    """Load project rules from AGENTS.md / CLAUDE.md and .shadowdev/rules/*.md."""
    if not workspace:
        return ""

    rules_parts = []

    # 1. Root-level rules files (AGENTS.md, CLAUDE.md, etc.)
    for name in getattr(config, "RULES_FILENAMES", ["AGENTS.md", "CLAUDE.md"]):
        path = os.path.join(workspace, name)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(10000)  # limit to 10KB
                rules_parts.append(f"## Project Rules ({name})\n{content}")
            except Exception:
                pass

    # 2. Rules directory: .shadowdev/rules/*.md (Continue.dev-style)
    rules_dir = os.path.join(workspace, ".shadowdev", "rules")
    if os.path.isdir(rules_dir):
        try:
            md_files = sorted(f for f in os.listdir(rules_dir) if f.endswith(".md"))
            for fname in md_files:
                fpath = os.path.join(rules_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(5000)  # 5KB per rule file
                    rule_name = fname.removesuffix(".md")
                    rules_parts.append(f"## Rule: {rule_name}\n{content}")
                except Exception:
                    pass
        except OSError:
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
    try:
        tool_name = recent[0][0][0] if recent and recent[0] else "unknown tool"
    except (IndexError, TypeError):
        tool_name = "unknown tool"

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


def _is_diminishing(state: "AgentState") -> bool:
    """Return True if agent is making diminishing progress (CC: isDiminishing).

    Triggers when:
    - 3+ consecutive continuation turns
    - Last 2 LLM response token deltas are both < 500 tokens
    """
    turns = state.get("session_turns", 0) if hasattr(state, "get") else getattr(state, "session_turns", 0)
    if turns < 3:
        return False

    messages = state.get("messages", []) if hasattr(state, "get") else list(getattr(state, "messages", []))
    # Collect AI messages that are not tool responses (proxy for agent response turns)
    ai_messages = [
        m for m in messages[-10:]
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None)
    ]
    if len(ai_messages) < 2:
        return False

    recent_sizes = [_msg_tokens(m) for m in ai_messages[-2:]]
    return all(size < 500 for size in recent_sizes)


# ─────────────────────────────────────────────────────────────
# Overflow detection
# ─────────────────────────────────────────────────────────────
def _get_model_limit() -> int:
    """Get token limit for current model."""
    provider = config.LLM_PROVIDER
    model_map = {
        "openai": "OPENAI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
        "google": "GOOGLE_MODEL",
        "groq": "GROQ_MODEL",
        "azure": "AZURE_OPENAI_MODEL",
    }
    attr = model_map.get(provider, "OLLAMA_MODEL")
    model_name = getattr(config, attr, config.OLLAMA_MODEL)
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
    protect_from = len(messages)  # default: protect everything (nothing to prune)
    for i in range(len(messages) - 1, -1, -1):
        tail_tokens += _msg_tokens(messages[i])
        if tail_tokens >= PRUNE_PROTECT:
            protect_from = i + 1  # protect from i onward (inclusive)
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


def _get_git_context(workspace: str) -> str:
    """Get current git state for context injection. Returns empty string if not in a git repo."""
    try:
        branch_r = _subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace or ".", capture_output=True, text=True, timeout=3
        )
        branch = branch_r.stdout.strip()
        if not branch or branch_r.returncode != 0:
            return ""

        log_r = _subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=workspace or ".", capture_output=True, text=True, timeout=3
        )
        status_r = _subprocess.run(
            ["git", "status", "--short"],
            cwd=workspace or ".", capture_output=True, text=True, timeout=3
        )
        parts = [f"Branch: {branch}"]
        if log_r.stdout.strip():
            parts.append(f"Recent commits:\n{log_r.stdout.strip()}")
        if status_r.stdout.strip():
            parts.append(f"Modified files:\n{status_r.stdout.strip()}")
        return "\n".join(parts)
    except FileNotFoundError:
        return ""  # git not installed — silent, expected
    except _subprocess.TimeoutExpired:
        return ""  # git hung — silent
    except PermissionError as e:
        logger.warning("git context: permission denied in workspace: %s", e)
        return ""
    except Exception:
        return ""  # other: silent


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

    # Diminishing-returns check (CC: isDiminishing) — stop if 3+ turns of tiny responses
    if _is_diminishing(state):
        logger.warning("[doom-loop] isDiminishing: 3+ turns with < 500-token responses — injecting recovery")
        recovery = (
            "I notice I've been making very little progress over the last few turns. "
            "Let me pause, reassess the goal, and take a more decisive next step."
        )
        return {
            "messages": [AIMessage(content=recovery)],
            "diminishing_halt": True,
        }

    # ── System prompt assembly (cache-ordering: stable → semi-stable → dynamic) ──
    #
    # Prompt caching (e.g. Anthropic prompt caching) benefits from a STABLE prefix
    # that doesn't change between turns/retries.  We therefore build system_content
    # in ascending order of volatility:
    #
    #   1. BASE_SYSTEM_PROMPT / PLANNER_PROMPT / CODER_PROMPT
    #      Fully static — never changes within a session. Best cache candidate.
    #
    #   2. Coordinator system prompt (static per session if mode doesn't change)
    #
    #   3. Project rules (AGENTS.md / CLAUDE.md / .shadowdev/rules/*.md)
    #      Changes only when the user edits those files — essentially static within
    #      a session. Small cache invalidation surface.
    #
    #   4. Model-aware edit instructions
    #      Static per model; changes only if the user switches provider mid-session.
    #
    #   5. Previous conversation summary — changes each compaction cycle (semi-stable)
    #
    #   6. Current workspace path — static per session
    #
    # Dynamic context (git status, current time, per-turn state) must NOT be prepended
    # before the stable base; appending it last keeps the cache-stable prefix intact.
    #
    # Build system message & pick LLM
    if state.active_agent == "planner":
        system_content = PLANNER_PROMPT  # 1. Stable base
        llm = llm_planner
    else:
        system_content = CODER_PROMPT    # 1. Stable base
        llm = llm_coder

    # Coordinator mode overrides both prompt and LLM binding
    if getattr(state, 'coordinator_mode', False) or is_coordinator_mode():
        system_content = get_coordinator_system_prompt()  # 2. Still stable per session
        llm = llm_planner  # coordinator delegates writes to workers, never does them directly

    # 3. Project rules (hierarchical: global → project → local → rules/)
    rules = load_project_rules(workspace=state.workspace or config.WORKSPACE_DIR)
    if rules:
        system_content += f"\n\n---\n## Project Rules\n{rules}"

    # 4. Model-aware edit instructions (static per provider/model)
    from agent.model_aware import get_edit_instruction
    edit_instr = get_edit_instruction()
    if edit_instr:
        system_content += edit_instr

    # 5. Previous conversation summary (semi-stable: changes only after compaction)
    if state.summary:
        system_content += f"\n\n## Previous Conversation Summary\n{state.summary}"
    # 6. Current workspace path (static per session)
    if state.workspace:
        system_content += f"\n\n## Current Workspace\n{state.workspace}"

    # 7. Dynamic git context (branch / recent commits / modified files)
    git_ctx = _get_git_context(state.workspace or config.WORKSPACE_DIR or ".")
    if git_ctx:
        system_content += f"\n\n## Current Git State\n{git_ctx}"

    # Build system message — add cache_control for Anthropic prompt caching
    # (cache_control on the system message covers the stable prompt prefix,
    # delivering up to 90% cost reduction on repeated/cached tokens)
    if config.LLM_PROVIDER == "anthropic":
        system_msg = SystemMessage(
            content=system_content,
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        )
    else:
        system_msg = SystemMessage(content=system_content)

    # Inject pending team worker notifications as HumanMessages
    notifications = getattr(state, 'team_notifications', [])
    injected_notifications = False
    if notifications and (getattr(state, 'coordinator_mode', False) or is_coordinator_mode()):
        extra_messages = [HumanMessage(content=n) for n in notifications]
        pruned = _prune_tool_outputs(messages)
        full_messages = [system_msg] + extra_messages + pruned
        injected_notifications = True
    else:
        full_messages = [system_msg] + _prune_tool_outputs(messages)

    try:
        response = await _invoke_with_retry(llm, full_messages)
    except FreeUsageLimitError as e:
        return {
            "messages": [AIMessage(
                content=(
                    f"\u26a0\ufe0f Free tier limit reached. Please upgrade your API plan "
                    f"or switch to a different provider.\n\nDetails: {e}"
                )
            )]
        }
    except ContextOverflowError as overflow_exc:
        # ── Reactive compaction (CC pattern) ─────────────────────────
        # If we've already tried compacting this turn, surface the error.
        if state.get("reactive_compact_attempted", False) if hasattr(state, "get") else getattr(state, "reactive_compact_attempted", False):
            logger.error("[reactive compact] overflow persists after compaction — surfacing error")
            raise overflow_exc.original from overflow_exc

        logger.warning("[reactive compact] context overflow detected, triggering compaction")
        compacted = await summarize_node(state)
        # Build a temporary merged state-like object so we can retry
        # We re-enter agent_node with the compacted messages by returning a
        # special marker; the graph will route back through agent_node.
        # To avoid infinite recursion we set reactive_compact_attempted=True.
        compacted["reactive_compact_attempted"] = True
        # Surface the compaction result now; LangGraph will call agent_node again
        # on the next step with the compacted messages, continuing transparently.
        return compacted

    response = _repair_tool_calls(response)

    active_agent = state.active_agent
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            if tc.get("name") == "handoff_to_coder":
                active_agent = "coder"
            elif tc.get("name") == "handoff_to_planner":
                active_agent = "planner"

    # Track token budget — prefer actual API usage over estimation (CC approach)
    # LangChain exposes this via response.usage_metadata or response.response_metadata
    turn_tokens = _msg_tokens(response)
    try:
        # usage_metadata: {"input_tokens": N, "output_tokens": M, ...} (Anthropic/OpenAI)
        # Use (x or 0) guards to handle None values for partial keys (e.g. only input_tokens)
        um = getattr(response, "usage_metadata", None)
        if um and isinstance(um, dict):
            total = (um.get("input_tokens", 0) or 0) + (um.get("output_tokens", 0) or 0)
            if total > 0:
                turn_tokens = total
        elif um and hasattr(um, "total_tokens"):
            turn_tokens = um.total_tokens or turn_tokens
    except Exception:
        pass  # estimation fallback is already set above
    new_turns = state.session_turns + 1
    new_tokens = state.total_tokens_used + turn_tokens

    # Track cost — determine active model name for pricing
    try:
        model_name = getattr(config, "LLM_MODEL", "")
        turn_cost = cost_from_response(model_name, response)
    except Exception:
        turn_cost = 0.0
    new_cost = getattr(state, "session_cost", 0.0) + turn_cost

    result = {
        "messages": [response],
        "active_agent": active_agent,
        "session_turns": new_turns,
        "total_tokens_used": new_tokens,
        "session_cost": new_cost,
        # Clear reactive compaction flag after a successful LLM call
        "reactive_compact_attempted": False,
    }

    # Clear injected notifications to prevent replay on the next turn
    if injected_notifications:
        result["team_notifications"] = []

    # ── Session memory extraction (fire-and-forget every N turns) ──
    global _session_memory_turn
    _session_memory_turn += 1
    if _session_memory_turn % _SESSION_MEMORY_INTERVAL == 0:
        import asyncio
        try:
            asyncio.ensure_future(_extract_session_memory(
                list(state.messages),
                state.workspace or config.WORKSPACE_DIR or ".",
            ))
        except Exception:
            pass  # extraction must never crash the agent

    # ── Auto Dream memory consolidation (fire-and-forget every 50 turns) ──
    try:
        from agent.auto_dream import run_auto_dream
        import asyncio as _aio
        _aio.ensure_future(run_auto_dream(list(state.messages), _session_memory_turn, llm))
    except Exception:
        pass  # auto dream must never crash the agent

    return result


# ─────────────────────────────────────────────────────────────
# Compaction template
# ─────────────────────────────────────────────────────────────
COMPACTION_TEMPLATE = """\
Your task is to create a concise but complete summary of a conversation so work can resume \
without loss of context. Output in two XML blocks: <analysis> for your private scratchpad \
(will be stripped), then <summary> with the final 9-section structured summary.

Conversation:
{conversation}

{previous_summary}

Output format (use EXACTLY these tags):

<analysis>
[Your private reasoning: what's important, what to include, what to cut. This section is \
discarded — use it to think clearly before writing the summary.]
</analysis>

<summary>
## 1. Primary Request and Intent
What the user wants to accomplish. The core goal driving this entire conversation. \
Capture the "why", not just the "what".

## 2. Key Technical Concepts
Key design decisions, patterns, algorithms, data structures, or APIs that are central \
to this work. Include non-obvious relationships between components.

## 3. Files and Code Sections
Files read or modified, with their purpose:
- path/to/file.py — what it does, why it matters, key functions/classes touched

## 4. Errors and Fixes
Errors encountered and how they were resolved. Include root cause if known.

## 5. Problem Solving
Approaches tried, dead ends, key insights. What worked and what didn't.

## 6. All User Messages
Verbatim or near-verbatim list of every user message in the conversation. \
This preserves their intent and tone exactly.

## 7. Pending Tasks
Tasks started but not finished, or explicitly planned but not yet started.

## 8. Current Work
Exact state of the work immediately before compaction: \
what was being done, what file was open, what step was in progress.

## 9. Optional Next Step
The single most important next action, if it is clear.
</summary>
"""

def _strip_images(messages: list) -> list:
    """Remove image_url content blocks and base64 image data from messages.
    Replaces with a placeholder: '[image stripped before compaction]'
    """
    stripped = []
    for msg in messages:
        if hasattr(msg, 'content') and isinstance(msg.content, list):
            new_content = []
            for block in msg.content:
                if isinstance(block, dict) and block.get('type') == 'image_url':
                    new_content.append({'type': 'text', 'text': '[image stripped before compaction]'})
                elif isinstance(block, dict) and block.get('type') == 'image':
                    new_content.append({'type': 'text', 'text': '[image stripped before compaction]'})
                else:
                    new_content.append(block)
            # Replace content but keep same message type
            extra = {k: v for k, v in vars(msg).items() if k != 'content'}
            msg = msg.__class__(content=new_content, **extra)
        stripped.append(msg)
    return stripped


def _extract_skill_names_from_messages(messages: list) -> list[str]:
    """Scan message history for skill_invoke tool calls and return unique skill names (order preserved)."""
    seen: dict[str, int] = {}  # name -> last-seen index
    for i, msg in enumerate(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if tc.get("name") == "skill_invoke":
                skill_name = tc.get("args", {}).get("name", "")
                if skill_name:
                    seen[skill_name] = i
    # Return names sorted by last-seen index ascending (LRU last), capped at 3
    sorted_names = sorted(seen, key=lambda n: seen[n])
    return sorted_names[-3:]


_SKILL_CONTENT_CAP = 4000   # 4 KB per skill
_SKILL_REINJECT_MAX = 3     # max skills to re-inject


def _build_skill_reinjection_messages(messages: list) -> list:
    """Re-inject the content of active skills that were invoked during the session.

    Returns up to _SKILL_REINJECT_MAX HumanMessages (one per skill).
    Total content capped at _SKILL_REINJECT_MAX x _SKILL_CONTENT_CAP.
    """
    from agent.skill_engine import invoke_skill

    names = _extract_skill_names_from_messages(messages)
    result = []
    for name in names[:_SKILL_REINJECT_MAX]:
        try:
            content, meta = invoke_skill(name)
            if meta is None:
                # Skill not found — skip silently
                continue
            snippet = content[:_SKILL_CONTENT_CAP]
            result.append(
                HumanMessage(
                    content=f"[Post-compact context] Active skill '{name}':\n{snippet}"
                )
            )
        except Exception as e:
            logger.warning(f"[compact] Failed to re-inject skill '{name}': {e}")
    return result


def _collect_recent_file_paths(messages: list, max_files: int = 5) -> list[str]:
    """Scan message history for file_read/file_write tool calls and return
    the most-recently-used unique file paths (MRU last, deduplicated)."""
    seen: dict[str, int] = {}  # path -> last-seen index
    for i, msg in enumerate(messages):
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if tc.get("name") in ("file_read", "file_write"):
                path = tc.get("args", {}).get("file_path", "")
                if path:
                    seen[path] = i  # overwrite with later index

    # Sort by last-seen index ascending (MRU last)
    sorted_paths = sorted(seen, key=lambda p: seen[p])
    # Keep only the most recent max_files
    return sorted_paths[-max_files:]


def _build_file_restoration_messages(messages: list, max_files: int = 5) -> list:
    """Return HumanMessages containing the current content of recently-used files.

    Skips files that no longer exist on disk.
    """
    paths = _collect_recent_file_paths(messages, max_files=max_files)
    restoration: list = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                # Cap at ~50 KB to stay within context budget
                content = fh.read(50_000)
            restoration.append(
                HumanMessage(
                    content=f"[Post-compact context] Contents of {path}:\n{content}"
                )
            )
        except Exception:
            pass
    return restoration


def _extract_compact_summary(raw: str) -> str:
    """Strip <analysis> block and unwrap <summary> tags — matches CC formatCompactSummary()."""
    # Remove <analysis>...</analysis>
    import re
    raw = re.sub(r"<analysis>.*?</analysis>", "", raw, flags=re.DOTALL).strip()
    # Unwrap <summary> tags, keep content
    m = re.search(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    # No tags — just return cleaned text (fallback for models that ignore format)
    return raw.strip()


# ─────────────────────────────────────────────────────────────
# Micro-compaction node (partial summary for recent overflow)
# ─────────────────────────────────────────────────────────────
# Soft threshold: condense when message count exceeds this but full
# compaction hasn't triggered yet (i.e. < COMPACTION_THRESHOLD).
_MICRO_COMPACT_THRESHOLD = 30


async def micro_compact_node(state: AgentState) -> dict:
    """Condense messages[10:-5] into a single AIMessage summary.

    Triggers when len(messages) > 30 AND full compaction is NOT needed.
    Keeps messages[0:10] (system + early context) and messages[-5:]
    (recent turns) intact; collapses the middle into one summary line.
    """
    messages = list(state.messages)
    n = len(messages)

    # Only act when in the soft-overflow range
    if n <= _MICRO_COMPACT_THRESHOLD or is_context_overflow(messages):
        return {}

    # Nothing to collapse if there aren't enough messages in the middle
    if n <= 15:  # need at least head(10) + tail(5) + 1 middle message
        return {}

    head = messages[:10]
    middle = messages[10:-5]
    tail = messages[-5:]

    # Build a compact one-line description of the middle messages
    parts = []
    for msg in middle:
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content.strip():
            snippet = content.strip()[:120].replace("\n", " ")
            parts.append(f"[{type(msg).__name__}] {snippet}")
        elif isinstance(content, list):
            text = " ".join(str(c) for c in content)[:120].replace("\n", " ")
            parts.append(f"[{type(msg).__name__}] {text}")

    summary_content = (
        f"[Micro-summary of steps 10–{n - 5}]: "
        + "; ".join(parts)
    )
    micro_summary_msg = AIMessage(content=summary_content)

    new_messages = head + [micro_summary_msg] + tail

    # Replace all current messages with the condensed list
    delete_msgs = []
    for m in messages:
        msg_id = getattr(m, "id", None)
        if not msg_id:
            logger.warning("micro_compact_node: skipping message without ID (type=%s)", type(m).__name__)
            continue
        delete_msgs.append(RemoveMessage(id=msg_id))

    global _prompt_cache_breaks
    _prompt_cache_breaks += 1
    logger.info(
        "Prompt cache break #%d (micro-compact): next request will not benefit from cached tokens",
        _prompt_cache_breaks,
    )

    return {"messages": delete_msgs + new_messages}


# ─────────────────────────────────────────────────────────────
# Summarize / Compact node
# ─────────────────────────────────────────────────────────────
async def summarize_node(state: AgentState) -> dict:
    """
    Smart compaction: uses token estimation + structured template.
    Falls back to message count if token estimation not available.
    """
    from agent.hooks import run_lifecycle_hook, LIFECYCLE_HOOKS

    if LIFECYCLE_HOOKS:
        await run_lifecycle_hook("pre_compact", {"message_count": len(state.messages)})

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

    # Strip images before passing to the summarization LLM to save tokens
    to_summarize_stripped = _strip_images(to_summarize)

    # Build conversation text for LLM
    conv_parts = []
    for msg in to_summarize_stripped:
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

    # Strip <analysis> scratchpad, extract <summary> content (CC formatCompactSummary)
    summary_text = _extract_compact_summary(response.content)

    # Remove old messages, keep recent ones
    delete_messages = [RemoveMessage(id=m.id) for m in to_summarize
                       if hasattr(m, "id") and m.id]

    # ── Post-compact file restoration ────────────────────────────
    # Re-inject the content of the last 5 unique files that were
    # read/written during the session, so the LLM retains file context.
    restoration_messages = _build_file_restoration_messages(messages)

    # ── Post-compact skill re-injection ──────────────────────────
    # Re-inject content of skills that were invoked during the session.
    # Capped at 3 skills x 4 KB = 12 KB max.
    skill_messages = _build_skill_reinjection_messages(messages)

    global _prompt_cache_breaks
    _prompt_cache_breaks += 1
    logger.info(
        "Prompt cache break #%d (summarize): next request will not benefit from cached tokens",
        _prompt_cache_breaks,
    )

    result = {
        "summary": summary_text,
        "messages": delete_messages + restoration_messages + skill_messages,
    }

    if LIFECYCLE_HOOKS:
        await run_lifecycle_hook("post_compact", {"summary_length": len(response.content)})

    return result


# [PromptIntel] -------------------------------------------------------
# Domain   : system_prompt
# CC source : template_literal (line ~147)
# Technique :
#   # Language
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : system_prompt
# CC source : template_literal (line ~189)
# Technique :
#   Tools are executed in a user-selected permission mode
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : memory
# CC source : template_literal (line ~19)
# Technique :
#   CRITICAL: Respond with TEXT ONLY
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : memory
# CC source : template_literal (line ~358)
# Technique :
#   ${baseSummary}
# [/PromptIntel] ------------------------------------------------------


# [PromptIntel] -------------------------------------------------------
# Domain   : memory
# CC source : template_literal (line ~365)
# Technique :
#   You are running in autonomous/proactive mode
# [/PromptIntel] ------------------------------------------------------
