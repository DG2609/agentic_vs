"""
tests/test_compaction.py

Tests for:
  Task 1 — Reactive compaction (_is_context_overflow helper, ContextOverflowError)
  Task 2 — isDiminishing doom-loop detection (_is_diminishing)
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


# ---------------------------------------------------------------------------
# Task 1 — _is_context_overflow + ContextOverflowError
# ---------------------------------------------------------------------------

class TestIsContextOverflow:
    """Tests for agent.nodes._is_context_overflow."""

    def _import(self):
        from agent.nodes import _is_context_overflow
        return _is_context_overflow

    def test_context_length_exceeded(self):
        _is_context_overflow = self._import()
        exc = Exception("This model's maximum context length is 4096 tokens (context_length_exceeded)")
        assert _is_context_overflow(exc) is True

    def test_prompt_too_long(self):
        _is_context_overflow = self._import()
        exc = Exception("prompt_too_long: request too large")
        assert _is_context_overflow(exc) is True

    def test_maximum_context_length(self):
        _is_context_overflow = self._import()
        exc = Exception("Exceeded maximum context length for this model")
        assert _is_context_overflow(exc) is True

    def test_context_window(self):
        _is_context_overflow = self._import()
        exc = Exception("context window exceeded — reduce your input")
        assert _is_context_overflow(exc) is True

    def test_too_many_tokens(self):
        _is_context_overflow = self._import()
        exc = Exception("Too many tokens in the request")
        assert _is_context_overflow(exc) is True

    def test_token_limit(self):
        _is_context_overflow = self._import()
        exc = Exception("token limit reached")
        assert _is_context_overflow(exc) is True

    def test_unrelated_error_returns_false(self):
        _is_context_overflow = self._import()
        exc = Exception("connection timed out")
        assert _is_context_overflow(exc) is False

    def test_rate_limit_error_returns_false(self):
        _is_context_overflow = self._import()
        exc = Exception("429 rate_limit exceeded")
        assert _is_context_overflow(exc) is False

    def test_overloaded_529_returns_false(self):
        _is_context_overflow = self._import()
        exc = Exception("529 overloaded")
        assert _is_context_overflow(exc) is False

    def test_case_insensitive(self):
        """Pattern matching should be case-insensitive."""
        _is_context_overflow = self._import()
        exc = Exception("CONTEXT_LENGTH_EXCEEDED")
        assert _is_context_overflow(exc) is True

    def test_empty_message_returns_false(self):
        _is_context_overflow = self._import()
        exc = Exception("")
        assert _is_context_overflow(exc) is False


class TestContextOverflowError:
    """Tests for the ContextOverflowError exception class."""

    def test_wraps_original(self):
        from agent.nodes import ContextOverflowError
        orig = ValueError("context_length_exceeded")
        err = ContextOverflowError(orig)
        assert err.original is orig

    def test_message_propagated(self):
        from agent.nodes import ContextOverflowError
        orig = RuntimeError("prompt_too_long")
        err = ContextOverflowError(orig)
        assert "prompt_too_long" in str(err)

    def test_is_exception_subclass(self):
        from agent.nodes import ContextOverflowError
        assert issubclass(ContextOverflowError, Exception)


# ---------------------------------------------------------------------------
# Task 2 — _is_diminishing
# ---------------------------------------------------------------------------

def _make_state(turns: int = 0, messages: list = None):
    """Create a minimal state-like object for _is_diminishing tests."""
    state = MagicMock()
    state.session_turns = turns
    state.messages = messages or []
    # Make state.get() delegate to attributes (mirrors AgentState dict-like access)
    def _get(key, default=None):
        return getattr(state, key, default)
    state.get = _get
    return state


def _make_ai_msg(content: str) -> AIMessage:
    """Create a plain AI message (no tool calls) with given content."""
    return AIMessage(content=content)


def _make_ai_tool_call_msg() -> AIMessage:
    """Create an AI message that has a tool call (should be excluded from size check)."""
    return AIMessage(
        content="",
        tool_calls=[{"name": "file_read", "args": {"file_path": "/tmp/x"}, "id": "tc1"}],
    )


class TestIsDiminishing:
    """Tests for agent.nodes._is_diminishing."""

    def _import(self):
        from agent.nodes import _is_diminishing
        return _is_diminishing

    def test_returns_false_when_fewer_than_3_turns(self):
        """_is_diminishing returns False when session_turns < 3."""
        _is_diminishing = self._import()
        state = _make_state(turns=2)
        assert _is_diminishing(state) is False

    def test_returns_false_when_exactly_0_turns(self):
        _is_diminishing = self._import()
        state = _make_state(turns=0)
        assert _is_diminishing(state) is False

    def test_returns_false_when_fewer_than_2_ai_messages(self):
        """Even with 3+ turns, needs at least 2 non-tool AI messages."""
        _is_diminishing = self._import()
        # Only one short AI message in history
        msgs = [_make_ai_msg("ok")]
        state = _make_state(turns=5, messages=msgs)
        assert _is_diminishing(state) is False

    def test_returns_true_when_last_2_ai_msgs_below_500_tokens(self):
        """Returns True when 3+ turns and last 2 AI responses are < 500 tokens each."""
        _is_diminishing = self._import()
        # Short messages — each well under 500 tokens (~<2000 chars)
        msgs = [
            _make_ai_msg("Short reply A."),
            _make_ai_msg("Short reply B."),
        ]
        state = _make_state(turns=3, messages=msgs)
        assert _is_diminishing(state) is True

    def test_returns_false_when_one_large_response(self):
        """Returns False when at least one of the last 2 AI responses is >= 500 tokens."""
        _is_diminishing = self._import()
        # One small, one large (> ~2000 chars ≈ 500 tokens)
        large_content = "x " * 1200  # ~2400 chars → ~600 tokens
        msgs = [
            _make_ai_msg("Short reply."),
            _make_ai_msg(large_content),
        ]
        state = _make_state(turns=5, messages=msgs)
        assert _is_diminishing(state) is False

    def test_returns_false_when_both_large_responses(self):
        """Returns False when both recent AI responses are large."""
        _is_diminishing = self._import()
        large = "word " * 800  # ~4000 chars → ~1000 tokens
        msgs = [_make_ai_msg(large), _make_ai_msg(large)]
        state = _make_state(turns=4, messages=msgs)
        assert _is_diminishing(state) is False

    def test_excludes_tool_call_messages(self):
        """AI messages that contain tool calls are not counted as response-turn proxies."""
        _is_diminishing = self._import()
        # Two tool-call messages (excluded) + only one plain response → not enough
        msgs = [
            _make_ai_tool_call_msg(),
            _make_ai_tool_call_msg(),
            _make_ai_msg("tiny"),
        ]
        state = _make_state(turns=4, messages=msgs)
        # Only 1 qualifying AI message → should return False
        assert _is_diminishing(state) is False

    def test_uses_last_10_messages_window(self):
        """Only the last 10 messages in history are examined."""
        _is_diminishing = self._import()
        # Stuff the history with 15 large responses, then end with 2 small ones
        large = "word " * 800
        msgs = [_make_ai_msg(large)] * 13 + [
            _make_ai_msg("tiny a"),
            _make_ai_msg("tiny b"),
        ]
        state = _make_state(turns=10, messages=msgs)
        # The last 10 window is: 8 large + 2 small → last 2 AI plain messages are small
        assert _is_diminishing(state) is True
