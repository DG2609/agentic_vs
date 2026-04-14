"""
Auto Dream — background memory consolidation service.
Inspired by Claude Code's autoDream service.

Periodically (every AUTO_DREAM_INTERVAL turns) runs a secondary LLM call that:
1. Reads the current session-memory.md
2. Finds new signals from recent conversation turns
3. Consolidates insights into a concise, durable memory file
4. Prunes stale / redundant entries

Activated when AUTO_DREAM_ENABLED=True in config (default True).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

AUTO_DREAM_INTERVAL = int(os.getenv("SHADOWDEV_AUTO_DREAM_INTERVAL", "50"))
_MEMORY_FILE = Path(config.WORKSPACE_DIR) / ".shadowdev" / "session-memory.md"
_LOCK: Optional[asyncio.Lock] = None  # lazy init to avoid event-loop binding at import

_CONSOLIDATION_PROMPT = """\
You are a memory consolidation agent. Your job is to maintain a concise, useful \
session-memory file for a software engineering AI assistant.

CURRENT MEMORY FILE:
{current_memory}

RECENT CONVERSATION CONTEXT (last {n_turns} turns):
{recent_context}

TASK:
1. Review the current memory and the recent conversation.
2. Extract NEW important facts, patterns, decisions, or user preferences.
3. Merge them into the existing memory — update stale entries, add new ones.
4. Remove redundant, contradictory, or no-longer-relevant entries.
5. Keep the output concise (under 100 lines). Use bullet points with clear categories.

OUTPUT: Write only the updated memory file content (no preamble, no explanation).
Use Markdown. Start with a brief timestamp comment: <!-- Updated: {timestamp} -->
"""


def _format_turns(messages: list) -> str:
    """Format last N messages into readable context."""
    lines = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            # Multipart content — extract text parts only
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content_str = str(content)[:500].strip()
        if content_str:
            lines.append(f"[{role}]: {content_str}")
    return "\n".join(lines)


async def run_auto_dream(
    messages: list,
    turn_count: int,
    llm: Optional[object] = None,
) -> bool:
    """Run memory consolidation if interval reached. Returns True if consolidation ran.

    Args:
        messages: Current conversation messages.
        turn_count: Current turn count.
        llm: LangChain LLM to use for consolidation. Uses fast model if None.
    """
    if not getattr(config, "AUTO_DREAM_ENABLED", True):
        return False

    if turn_count % AUTO_DREAM_INTERVAL != 0 or turn_count == 0:
        return False

    # Prevent concurrent consolidation runs — create lock lazily per event loop
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    if _LOCK.locked():
        logger.debug("Auto dream skipped — consolidation already in progress")
        return False

    async with _LOCK:
        try:
            return await _consolidate(messages, llm)
        except Exception as e:
            logger.warning("Auto dream failed: %s", e)
            return False


async def _consolidate(messages: list, llm: Optional[object]) -> bool:
    """Perform the actual consolidation."""
    from langchain_core.messages import HumanMessage, SystemMessage

    # Read current memory
    current_memory = ""
    if _MEMORY_FILE.exists():
        try:
            current_memory = _MEMORY_FILE.read_text(encoding="utf-8")
        except OSError:
            pass

    if not current_memory and not messages:
        return False

    # Take last 20 messages for context
    recent = messages[-20:] if len(messages) > 20 else messages
    recent_context = _format_turns(recent)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    prompt = _CONSOLIDATION_PROMPT.format(
        current_memory=current_memory[:3000] or "(empty)",
        recent_context=recent_context[:2000] or "(none)",
        n_turns=len(recent),
        timestamp=timestamp,
    )

    # Use provided LLM or build a fast one
    if llm is None:
        try:
            llm = _build_fast_llm()
        except Exception as e:
            logger.debug("Auto dream could not build LLM: %s", e)
            return False

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content="You are a concise memory consolidation agent."),
                HumanMessage(content=prompt),
            ]),
            timeout=45.0,
        )
        consolidated = str(getattr(response, "content", response)).strip()
        if consolidated:
            _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _MEMORY_FILE.write_text(consolidated, encoding="utf-8")
            logger.info("Auto dream: memory consolidated (%d chars)", len(consolidated))
            return True
    except asyncio.TimeoutError:
        logger.debug("Auto dream timed out after 45s")
    except Exception as e:
        logger.debug("Auto dream LLM call failed: %s", e)

    return False


def _build_fast_llm():
    """Build a fast/cheap LLM for consolidation."""
    provider = config.LLM_PROVIDER

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=getattr(config, "FAST_MODEL", "claude-haiku-4-5-20251001"),
            api_key=config.ANTHROPIC_API_KEY,
            max_tokens=1024,
            temperature=0.1,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=getattr(config, "FAST_MODEL", "gpt-4o-mini"),
            api_key=config.OPENAI_API_KEY,
            max_tokens=1024,
            temperature=0.1,
        )
    else:
        raise ValueError(f"Auto dream not supported for provider: {provider}")


def get_memory_content() -> str:
    """Return current session-memory.md content (empty string if not found)."""
    if _MEMORY_FILE.exists():
        try:
            return _MEMORY_FILE.read_text(encoding="utf-8")
        except OSError:
            pass
    return ""
