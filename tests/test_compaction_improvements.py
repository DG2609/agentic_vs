"""
tests/test_compaction_improvements.py

Tests for compaction pipeline improvements:
  - Image stripping before summarisation LLM call
  - Post-compact skill re-injection
"""
import sys
import os

# Ensure project root is on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ai_with_tool_call(tool_name: str, args: dict, msg_id: str = "tc1") -> AIMessage:
    """Create an AIMessage that contains a tool call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args, "id": msg_id}],
    )


# ---------------------------------------------------------------------------
# Task 1 — _strip_images
# ---------------------------------------------------------------------------

class TestStripImages:
    """Tests for agent.nodes._strip_images."""

    def _import(self):
        from agent.nodes import _strip_images
        return _strip_images

    def test_strip_images_removes_image_url(self):
        """image_url blocks are replaced with the placeholder text."""
        _strip_images = self._import()

        msg = HumanMessage(content=[
            {"type": "text", "text": "Look at this:"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ])
        result = _strip_images([msg])

        assert len(result) == 1
        content = result[0].content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "Look at this:"}
        assert content[1] == {"type": "text", "text": "[image stripped before compaction]"}

    def test_strip_images_removes_image_block(self):
        """Blocks of type 'image' (Anthropic-style) are also stripped."""
        _strip_images = self._import()

        msg = HumanMessage(content=[
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "xyz"}},
            {"type": "text", "text": "caption"},
        ])
        result = _strip_images([msg])

        content = result[0].content
        assert content[0] == {"type": "text", "text": "[image stripped before compaction]"}
        assert content[1] == {"type": "text", "text": "caption"}

    def test_strip_images_preserves_text(self):
        """Non-image content blocks are unchanged."""
        _strip_images = self._import()

        original_blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "id": "1", "name": "file_read", "input": {}},
        ]
        msg = HumanMessage(content=list(original_blocks))
        result = _strip_images([msg])

        assert result[0].content == original_blocks

    def test_strip_images_handles_string_content(self):
        """Messages with plain string content are passed through unchanged."""
        _strip_images = self._import()

        msg = HumanMessage(content="Just a plain text message with no images.")
        result = _strip_images([msg])

        assert len(result) == 1
        assert result[0].content == "Just a plain text message with no images."

    def test_strip_images_preserves_message_type(self):
        """The returned message is the same class as the input."""
        _strip_images = self._import()

        ai_msg = AIMessage(content=[
            {"type": "text", "text": "thinking…"},
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
        ])
        result = _strip_images([ai_msg])

        assert isinstance(result[0], AIMessage)

    def test_strip_images_multiple_messages(self):
        """Processes a list of mixed messages correctly."""
        _strip_images = self._import()

        msgs = [
            HumanMessage(content="plain"),
            HumanMessage(content=[
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                {"type": "text", "text": "after image"},
            ]),
            AIMessage(content="response"),
        ]
        result = _strip_images(msgs)

        assert len(result) == 3
        assert result[0].content == "plain"
        assert result[1].content[0]["text"] == "[image stripped before compaction]"
        assert result[1].content[1]["text"] == "after image"
        assert result[2].content == "response"


# ---------------------------------------------------------------------------
# Task 2 — Skill re-injection helpers
# ---------------------------------------------------------------------------

class TestSkillReinjection:
    """Tests for _extract_skill_names_from_messages and _build_skill_reinjection_messages."""

    def _import(self):
        from agent.nodes import (
            _extract_skill_names_from_messages,
            _build_skill_reinjection_messages,
        )
        return _extract_skill_names_from_messages, _build_skill_reinjection_messages

    # --- _extract_skill_names_from_messages ---

    def test_extract_skill_names_empty(self):
        """Returns empty list when no skill_invoke calls are present."""
        extract, _ = self._import()
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert extract(msgs) == []

    def test_extract_skill_names_single(self):
        """Returns the skill name from a skill_invoke tool call."""
        extract, _ = self._import()
        ai = _make_ai_with_tool_call("skill_invoke", {"name": "commit-workflow"})
        assert extract([ai]) == ["commit-workflow"]

    def test_extract_skill_names_dedup_and_order(self):
        """Duplicate invocations are deduped; order follows last-seen index."""
        extract, _ = self._import()
        msgs = [
            _make_ai_with_tool_call("skill_invoke", {"name": "audit"}, "t1"),
            _make_ai_with_tool_call("skill_invoke", {"name": "refactor"}, "t2"),
            _make_ai_with_tool_call("skill_invoke", {"name": "audit"}, "t3"),  # re-invoked later
        ]
        names = extract(msgs)
        # 'audit' was last seen at index 2, 'refactor' at index 1 → order: refactor, audit
        assert names == ["refactor", "audit"]

    def test_extract_skill_names_caps_at_three(self):
        """Returns at most 3 skill names (the 3 most recently invoked)."""
        extract, _ = self._import()
        msgs = [
            _make_ai_with_tool_call("skill_invoke", {"name": "a"}, "t0"),
            _make_ai_with_tool_call("skill_invoke", {"name": "b"}, "t1"),
            _make_ai_with_tool_call("skill_invoke", {"name": "c"}, "t2"),
            _make_ai_with_tool_call("skill_invoke", {"name": "d"}, "t3"),
        ]
        names = extract(msgs)
        assert len(names) == 3
        # Should be the last 3: b, c, d
        assert names == ["b", "c", "d"]

    def test_extract_ignores_non_skill_tool_calls(self):
        """Tool calls other than skill_invoke are ignored."""
        extract, _ = self._import()
        msgs = [
            _make_ai_with_tool_call("file_read", {"file_path": "/tmp/foo.py"}, "t1"),
            _make_ai_with_tool_call("skill_invoke", {"name": "my-skill"}, "t2"),
        ]
        names = extract(msgs)
        assert names == ["my-skill"]

    # --- _build_skill_reinjection_messages ---

    def test_skill_reinjection_after_compact(self):
        """Mock skill lookup; verify a HumanMessage is returned with skill content."""
        _, build = self._import()

        ai_msg = _make_ai_with_tool_call("skill_invoke", {"name": "my-skill"}, "t1")

        fake_content = "# My Skill\nDo the thing step by step."
        fake_meta = MagicMock()
        fake_meta.name = "my-skill"

        with patch("agent.skill_engine.invoke_skill", return_value=(fake_content, fake_meta)) as mock_invoke:
            result = build([ai_msg])

        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        assert "my-skill" in result[0].content
        assert fake_content in result[0].content
        mock_invoke.assert_called_once_with("my-skill")

    def test_skill_reinjection_skips_missing_skill(self):
        """When invoke_skill returns (msg, None), the skill is silently skipped."""
        _, build = self._import()

        ai_msg = _make_ai_with_tool_call("skill_invoke", {"name": "ghost-skill"}, "t1")

        with patch("agent.skill_engine.invoke_skill", return_value=("Skill not found.", None)):
            result = build([ai_msg])

        assert result == []

    def test_skill_reinjection_caps_content_at_4000_chars(self):
        """Skill content is truncated to 4000 characters."""
        _, build = self._import()

        ai_msg = _make_ai_with_tool_call("skill_invoke", {"name": "big-skill"}, "t1")

        long_content = "x" * 10_000
        fake_meta = MagicMock()
        fake_meta.name = "big-skill"

        with patch("agent.skill_engine.invoke_skill", return_value=(long_content, fake_meta)):
            result = build([ai_msg])

        assert len(result) == 1
        # Content in the message should be capped at 4000 chars plus the prefix
        injected = result[0].content
        skill_part = injected.split(":\n", 1)[1]
        assert len(skill_part) <= 4000

    def test_skill_reinjection_max_three_skills(self):
        """Re-injection is capped at 3 skills."""
        _, build = self._import()

        msgs = [
            _make_ai_with_tool_call("skill_invoke", {"name": f"skill-{i}"}, f"t{i}")
            for i in range(5)
        ]

        fake_meta = MagicMock()

        def fake_invoke(name):
            fake_meta.name = name
            return (f"content for {name}", fake_meta)

        with patch("agent.skill_engine.invoke_skill", side_effect=fake_invoke):
            result = build(msgs)

        assert len(result) == 3

    def test_skill_reinjection_handles_exception(self):
        """If invoke_skill raises, the skill is silently skipped (no crash)."""
        _, build = self._import()

        ai_msg = _make_ai_with_tool_call("skill_invoke", {"name": "boom"}, "t1")

        with patch("agent.skill_engine.invoke_skill", side_effect=RuntimeError("disk error")):
            result = build([ai_msg])

        assert result == []
