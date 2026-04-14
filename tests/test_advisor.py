"""Tests for the model advisor system."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# set_advisor_model / get_advisor_model
# ---------------------------------------------------------------------------

def test_set_and_get_advisor_model():
    from agent.advisor import set_advisor_model, get_advisor_model
    set_advisor_model("claude-opus-4-6")
    assert get_advisor_model() == "claude-opus-4-6"
    set_advisor_model("")  # cleanup
    assert get_advisor_model() == ""


def test_set_advisor_model_strips_whitespace():
    from agent.advisor import set_advisor_model, get_advisor_model
    set_advisor_model("  gpt-4o  ")
    assert get_advisor_model() == "gpt-4o"
    set_advisor_model("")  # cleanup


def test_disable_advisor_with_empty_string():
    from agent.advisor import set_advisor_model, get_advisor_model
    set_advisor_model("claude-opus-4-6")
    set_advisor_model("")
    assert get_advisor_model() == ""


def test_get_advisor_model_returns_string():
    from agent.advisor import get_advisor_model
    result = get_advisor_model()
    assert isinstance(result, str)


def test_set_advisor_model_multiple_updates():
    """Last set wins — no accumulation."""
    from agent.advisor import set_advisor_model, get_advisor_model
    set_advisor_model("model-a")
    set_advisor_model("model-b")
    assert get_advisor_model() == "model-b"
    set_advisor_model("")  # cleanup


# ---------------------------------------------------------------------------
# run_advisor — async tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_advisor_returns_none_on_exception():
    """run_advisor must never raise — returns None on any failure."""
    from agent.advisor import run_advisor
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "anthropic"
        mock_cfg.ANTHROPIC_API_KEY = "test-key"
        with patch("agent.advisor.ChatAnthropic", side_effect=Exception("API error")):
            result = await run_advisor("hello", "world", "claude-opus-4-6")
    assert result is None


@pytest.mark.asyncio
async def test_run_advisor_returns_none_for_unsupported_provider():
    """Providers other than anthropic/openai return None without raising."""
    from agent.advisor import run_advisor
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "ollama"
        result = await run_advisor("prompt", "response", "llama3")
    assert result is None


@pytest.mark.asyncio
async def test_run_advisor_returns_none_for_groq_provider():
    """Groq is also unsupported — returns None."""
    from agent.advisor import run_advisor
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "groq"
        result = await run_advisor("prompt", "response", "llama3-8b")
    assert result is None


@pytest.mark.asyncio
async def test_run_advisor_returns_string_on_success():
    """On success, run_advisor returns the LLM's critique text."""
    from agent.advisor import run_advisor
    mock_response = MagicMock()
    mock_response.content = "LGTM \u2705 The response looks correct."
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "anthropic"
        mock_cfg.ANTHROPIC_API_KEY = "test-key"
        with patch("agent.advisor.ChatAnthropic", return_value=mock_llm):
            result = await run_advisor(
                "What is 2+2?",
                "The answer is 4.",
                "claude-haiku-4-5",
            )
    assert result == "LGTM \u2705 The response looks correct."


@pytest.mark.asyncio
async def test_run_advisor_openai_provider_success():
    """run_advisor works for openai provider."""
    from agent.advisor import run_advisor
    mock_response = MagicMock()
    mock_response.content = "Looks good."
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "openai"
        mock_cfg.OPENAI_API_KEY = "sk-test"
        with patch("agent.advisor.ChatOpenAI", return_value=mock_llm):
            result = await run_advisor("Hello?", "Hi!", "gpt-4o")
    assert result == "Looks good."


@pytest.mark.asyncio
async def test_run_advisor_openai_returns_none_on_exception():
    """run_advisor swallows exceptions for openai provider too."""
    from agent.advisor import run_advisor
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "openai"
        mock_cfg.OPENAI_API_KEY = "sk-test"
        with patch("agent.advisor.ChatOpenAI", side_effect=RuntimeError("timeout")):
            result = await run_advisor("q", "a", "gpt-4o")
    assert result is None


@pytest.mark.asyncio
async def test_run_advisor_strips_whitespace_from_response():
    """Response content is stripped of surrounding whitespace."""
    from agent.advisor import run_advisor
    mock_response = MagicMock()
    mock_response.content = "  \n  Great answer.  \n  "
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "anthropic"
        mock_cfg.ANTHROPIC_API_KEY = "test-key"
        with patch("agent.advisor.ChatAnthropic", return_value=mock_llm):
            result = await run_advisor("q", "a", "claude-haiku-4-5")
    assert result == "Great answer."


@pytest.mark.asyncio
async def test_run_advisor_timeout_returns_none():
    """asyncio.TimeoutError from wait_for is caught and returns None."""
    from agent.advisor import run_advisor
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())
    with patch("agent.advisor.config") as mock_cfg:
        mock_cfg.LLM_PROVIDER = "anthropic"
        mock_cfg.ANTHROPIC_API_KEY = "test-key"
        with patch("agent.advisor.ChatAnthropic", return_value=mock_llm):
            result = await run_advisor("q", "a", "claude-opus-4-6")
    assert result is None


# ---------------------------------------------------------------------------
# Additional: todo dependency tracking
# ---------------------------------------------------------------------------

def test_todo_write_with_depends_on():
    from agent.tools.todo import todo_write, todo_read, _set_todos
    _set_todos([])
    result = todo_write.invoke({"todos": [
        {"id": 1, "content": "Step A", "status": "pending"},
        {"id": 2, "content": "Step B", "status": "pending", "depends_on": [1]},
    ]})
    assert "updated" in result.lower()
    read_result = todo_read.invoke({})
    assert "blocked by: 1" in read_result
    _set_todos([])


def test_todo_write_invalid_depends_on_rejected():
    from agent.tools.todo import todo_write, _set_todos
    _set_todos([])
    result = todo_write.invoke({"todos": [
        {"id": 1, "content": "task", "status": "pending", "depends_on": [99]},
    ]})
    assert "Error" in result or "unknown" in result.lower()
    _set_todos([])


def test_todo_blocked_resolves_when_dependency_complete():
    from agent.tools.todo import todo_write, todo_read, _set_todos
    _set_todos([])
    todo_write.invoke({"todos": [
        {"id": 1, "content": "Step A", "status": "completed"},
        {"id": 2, "content": "Step B", "status": "pending", "depends_on": [1]},
    ]})
    read_result = todo_read.invoke({})
    assert "blocked by" not in read_result
    _set_todos([])
