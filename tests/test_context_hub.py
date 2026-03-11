"""Tests for agent/tools/context_hub.py — Context Hub integration."""
import json
from unittest.mock import patch, MagicMock

import pytest


def _mock_run(stdout="", stderr="", returncode=0):
    """Create a mock subprocess.run result."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class TestChubSearch:
    def test_search_basic(self):
        from agent.tools.context_hub import chub_search
        data = {
            "total": 2,
            "results": [
                {"id": "stripe/api", "type": "doc", "description": "Stripe API reference",
                 "languages": [{"language": "python"}, {"language": "javascript"}],
                 "tags": ["payments", "stripe"]},
                {"id": "stripe/webhooks", "type": "doc", "description": "Stripe Webhooks",
                 "languages": [{"language": "python"}], "tags": ["payments"]},
            ]
        }
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}):
            result = chub_search.invoke({"query": "stripe"})
        assert "stripe/api" in result
        assert "stripe/webhooks" in result
        assert "python, javascript" in result
        assert "Found 2" in result

    def test_search_no_results(self):
        from agent.tools.context_hub import chub_search
        data = {"total": 0, "results": []}
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}):
            result = chub_search.invoke({"query": "nonexistent"})
        assert "No results" in result

    def test_search_empty_query_lists_all(self):
        from agent.tools.context_hub import chub_search
        data = {"total": 68, "results": [{"id": "openai/chat", "type": "doc", "description": "OpenAI", "tags": []}]}
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}) as mock:
            chub_search.invoke({"query": ""})
            call_args = mock.call_args[0][0]
            assert call_args == ["search"]

    def test_search_with_tags(self):
        from agent.tools.context_hub import chub_search
        data = {"total": 1, "results": [{"id": "openai/chat", "type": "doc", "description": "test", "tags": ["ai"]}]}
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}) as mock:
            chub_search.invoke({"query": "api", "tags": "ai,llm"})
            call_args = mock.call_args[0][0]
            assert "--tags" in call_args
            assert "ai,llm" in call_args

    def test_search_error(self):
        from agent.tools.context_hub import chub_search
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": False, "error": "not found"}):
            result = chub_search.invoke({"query": "test"})
        assert "Error" in result


class TestChubGet:
    def test_get_basic(self):
        from agent.tools.context_hub import chub_get
        data = {
            "id": "stripe/api",
            "type": "doc",
            "content": "# Stripe API\n\nCreate charges with...",
            "additionalFiles": ["references/auth.md", "references/webhooks.md"],
        }
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}):
            result = chub_get.invoke({"entry_id": "stripe/api", "lang": "py"})
        assert "Stripe API" in result
        assert "references/auth.md" in result

    def test_get_with_annotation(self):
        from agent.tools.context_hub import chub_get
        data = {
            "id": "stripe/api",
            "content": "# Stripe API docs",
            "annotation": {"note": "Use raw body for webhooks", "updatedAt": "2026-03-10"},
        }
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": data, "output": ""}):
            result = chub_get.invoke({"entry_id": "stripe/api"})
        assert "Agent note" in result
        assert "raw body for webhooks" in result

    def test_get_with_lang_and_version(self):
        from agent.tools.context_hub import chub_get
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": {"content": "ok"}, "output": ""}) as mock:
            chub_get.invoke({"entry_id": "openai/chat", "lang": "js", "version": "4.0.0"})
            call_args = mock.call_args[0][0]
            assert "--lang" in call_args
            assert "js" in call_args
            assert "--version" in call_args
            assert "4.0.0" in call_args

    def test_get_full_flag(self):
        from agent.tools.context_hub import chub_get
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": {"content": "ok"}, "output": ""}) as mock:
            chub_get.invoke({"entry_id": "openai/chat", "full": True})
            call_args = mock.call_args[0][0]
            assert "--full" in call_args

    def test_get_specific_file(self):
        from agent.tools.context_hub import chub_get
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "data": {"content": "auth docs"}, "output": ""}) as mock:
            chub_get.invoke({"entry_id": "stripe/api", "file": "references/auth.md"})
            call_args = mock.call_args[0][0]
            assert "--file" in call_args
            assert "references/auth.md" in call_args

    def test_get_error(self):
        from agent.tools.context_hub import chub_get
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": False, "error": "not installed"}):
            result = chub_get.invoke({"entry_id": "stripe/api"})
        assert "Error" in result


class TestChubAnnotate:
    def test_annotate_save(self):
        from agent.tools.context_hub import chub_annotate
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "Annotation saved."}) as mock:
            result = chub_annotate.invoke({"entry_id": "stripe/api", "note": "needs raw body"})
            call_args = mock.call_args[0][0]
            assert "annotate" in call_args
            assert "stripe/api" in call_args
            assert "needs raw body" in call_args
        assert "saved" in result.lower() or "Done" in result

    def test_annotate_read(self):
        from agent.tools.context_hub import chub_annotate
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "Use raw body for webhooks"}) as mock:
            result = chub_annotate.invoke({"entry_id": "stripe/api"})
        assert "raw body" in result or "Done" in result

    def test_annotate_clear(self):
        from agent.tools.context_hub import chub_annotate
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "Cleared."}) as mock:
            chub_annotate.invoke({"entry_id": "stripe/api", "clear": True})
            call_args = mock.call_args[0][0]
            assert "--clear" in call_args

    def test_annotate_list_all(self):
        from agent.tools.context_hub import chub_annotate
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "annotations..."}) as mock:
            chub_annotate.invoke({"entry_id": "", "list_all": True})
            call_args = mock.call_args[0][0]
            assert "--list" in call_args


class TestChubFeedback:
    def test_feedback_up(self):
        from agent.tools.context_hub import chub_feedback
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "Feedback sent"}) as mock:
            result = chub_feedback.invoke({"entry_id": "stripe/api", "rating": "up"})
            call_args = mock.call_args[0][0]
            assert "feedback" in call_args
            assert "up" in call_args
        assert "sent" in result.lower() or "up" in result.lower()

    def test_feedback_down_with_labels(self):
        from agent.tools.context_hub import chub_feedback
        with patch("agent.tools.context_hub._run_chub", return_value={"ok": True, "output": "Feedback sent"}) as mock:
            chub_feedback.invoke({
                "entry_id": "openai/chat",
                "rating": "down",
                "comment": "Examples broken",
                "labels": "outdated,wrong-examples",
            })
            call_args = mock.call_args[0][0]
            assert "--label" in call_args
            assert "outdated" in call_args
            assert "wrong-examples" in call_args

    def test_feedback_invalid_rating(self):
        from agent.tools.context_hub import chub_feedback
        result = chub_feedback.invoke({"entry_id": "x", "rating": "maybe"})
        assert "Error" in result


class TestRunChub:
    def test_chub_not_found(self):
        from agent.tools.context_hub import _run_chub
        with patch("agent.tools.context_hub._find_chub", return_value="chub"), \
             patch("agent.tools.context_hub.subprocess.run", side_effect=FileNotFoundError):
            result = _run_chub(["search", "test"])
        assert not result["ok"]
        assert "not found" in result["error"].lower()

    def test_chub_timeout(self):
        import subprocess
        from agent.tools.context_hub import _run_chub
        with patch("agent.tools.context_hub._find_chub", return_value="chub"), \
             patch("agent.tools.context_hub.subprocess.run", side_effect=subprocess.TimeoutExpired("chub", 30)):
            result = _run_chub(["get", "stripe/api"])
        assert not result["ok"]
        assert "timed out" in result["error"]

    def test_chub_json_parse(self):
        from agent.tools.context_hub import _run_chub
        mock_result = _mock_run(stdout='{"total": 5, "results": []}')
        with patch("agent.tools.context_hub._find_chub", return_value="chub"), \
             patch("agent.tools.context_hub.subprocess.run", return_value=mock_result):
            result = _run_chub(["search"])
        assert result["ok"]
        assert result["data"]["total"] == 5

    def test_uses_npx_fallback(self):
        from agent.tools import context_hub
        context_hub._CHUB_PATH = None  # Reset cache
        with patch("agent.tools.context_hub.shutil.which", return_value=None):
            path = context_hub._find_chub()
        assert path == "npx"
        context_hub._CHUB_PATH = None  # Clean up

    def test_uses_global_install(self):
        from agent.tools import context_hub
        context_hub._CHUB_PATH = None
        with patch("agent.tools.context_hub.shutil.which", return_value="/usr/local/bin/chub"):
            path = context_hub._find_chub()
        assert path == "/usr/local/bin/chub"
        context_hub._CHUB_PATH = None
