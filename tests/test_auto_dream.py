"""Tests for the Auto Dream memory consolidation service."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ── get_memory_content ───────────────────────────────────────────────────────

def test_get_memory_content_returns_empty_when_file_missing(tmp_path):
    from agent.auto_dream import get_memory_content
    with patch("agent.auto_dream._MEMORY_FILE", tmp_path / "nonexistent.md"):
        result = get_memory_content()
    assert result == ""


def test_get_memory_content_returns_file_content(tmp_path):
    from agent.auto_dream import get_memory_content
    mem_file = tmp_path / "session-memory.md"
    mem_file.write_text("# Memory\n- foo bar", encoding="utf-8")
    with patch("agent.auto_dream._MEMORY_FILE", mem_file):
        result = get_memory_content()
    assert "foo bar" in result


# ── run_auto_dream ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_auto_dream_skips_wrong_interval():
    """Should not consolidate on non-interval turns."""
    from agent.auto_dream import run_auto_dream
    with patch("agent.auto_dream.config") as mock_cfg:
        mock_cfg.AUTO_DREAM_ENABLED = True
        result = await run_auto_dream([], turn_count=7)  # not multiple of 50
    assert result is False


@pytest.mark.asyncio
async def test_run_auto_dream_skips_when_disabled():
    from agent.auto_dream import run_auto_dream
    with patch("agent.auto_dream.config") as mock_cfg:
        mock_cfg.AUTO_DREAM_ENABLED = False
        result = await run_auto_dream([], turn_count=50)
    assert result is False


@pytest.mark.asyncio
async def test_run_auto_dream_skips_turn_zero():
    from agent.auto_dream import run_auto_dream
    with patch("agent.auto_dream.config") as mock_cfg:
        mock_cfg.AUTO_DREAM_ENABLED = True
        result = await run_auto_dream([], turn_count=0)
    assert result is False


@pytest.mark.asyncio
async def test_run_auto_dream_consolidates_at_interval(tmp_path):
    """At turn 50 with a mock LLM, consolidation should run and write file."""
    import agent.auto_dream as ad
    ad._LOCK = None  # reset lazy lock for clean test

    mock_response = MagicMock()
    mock_response.content = "# Consolidated memory\n- item 1"
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    from langchain_core.messages import HumanMessage, AIMessage
    messages = [
        HumanMessage(content="Build me a REST API."),
        AIMessage(content="Sure, here's the plan..."),
    ]
    mem_file = tmp_path / "session-memory.md"
    with patch("agent.auto_dream.config") as mock_cfg, \
         patch("agent.auto_dream._MEMORY_FILE", mem_file):
        mock_cfg.AUTO_DREAM_ENABLED = True
        result = await ad.run_auto_dream(messages, turn_count=50, llm=mock_llm)

    assert result is True
    assert mem_file.exists()
    assert "Consolidated memory" in mem_file.read_text()


@pytest.mark.asyncio
async def test_run_auto_dream_returns_false_on_llm_failure(tmp_path):
    """LLM failure should return False, not raise."""
    import agent.auto_dream as ad
    ad._LOCK = None
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("API down"))

    mem_file = tmp_path / "missing.md"
    with patch("agent.auto_dream.config") as mock_cfg, \
         patch("agent.auto_dream._MEMORY_FILE", mem_file):
        mock_cfg.AUTO_DREAM_ENABLED = True
        result = await ad.run_auto_dream([], turn_count=50, llm=mock_llm)

    assert result is False


@pytest.mark.asyncio
async def test_run_auto_dream_timeout_returns_false(tmp_path):
    """Timeout from wait_for is swallowed, returns False."""
    import agent.auto_dream as ad
    ad._LOCK = None
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())

    mem_file = tmp_path / "missing.md"
    with patch("agent.auto_dream.config") as mock_cfg, \
         patch("agent.auto_dream._MEMORY_FILE", mem_file):
        mock_cfg.AUTO_DREAM_ENABLED = True
        result = await ad.run_auto_dream([], turn_count=50, llm=mock_llm)

    assert result is False


# ── _format_turns ────────────────────────────────────────────────────────────

def test_format_turns_handles_text_messages():
    from agent.auto_dream import _format_turns
    from langchain_core.messages import HumanMessage, AIMessage
    messages = [
        HumanMessage(content="What is 2+2?"),
        AIMessage(content="The answer is 4."),
    ]
    result = _format_turns(messages)
    assert "2+2" in result
    assert "answer is 4" in result


def test_format_turns_handles_empty_list():
    from agent.auto_dream import _format_turns
    result = _format_turns([])
    assert result == ""


def test_format_turns_truncates_long_content():
    from agent.auto_dream import _format_turns
    from langchain_core.messages import HumanMessage
    long_msg = HumanMessage(content="x" * 2000)
    result = _format_turns([long_msg])
    assert len(result) <= 600  # truncated to 500 chars + role prefix


def test_format_turns_handles_multipart_content():
    from agent.auto_dream import _format_turns
    from langchain_core.messages import HumanMessage
    msg = MagicMock()
    msg.type = "human"
    msg.content = [{"text": "part one"}, {"text": "part two"}]
    result = _format_turns([msg])
    assert "part one" in result
    assert "part two" in result
