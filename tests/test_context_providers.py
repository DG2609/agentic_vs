"""Tests for agent/context_providers.py — @file, @diff, @codebase expansion."""
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a temp workspace with sample files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "main.py").write_text("def main():\n    print('hello')\n")
    (ws / "utils.py").write_text("def helper():\n    return 42\n")
    (ws / "big.txt").write_text("line\n" * 600)
    return str(ws)


class TestExpandContextMentions:
    def test_no_mentions_passthrough(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("just a normal prompt", workspace)
        assert result == "just a normal prompt"

    def test_file_mention(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("check @file:main.py please", workspace)
        assert "def main():" in result
        assert "Attached context" in result
        assert "# main.py" in result

    def test_file_with_line_range(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("look at @file:main.py:1-1", workspace)
        assert "def main():" in result
        assert "lines 1-1" in result

    def test_file_not_found(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("check @file:missing.py", workspace)
        assert "File not found" in result

    def test_file_path_traversal_blocked(self, workspace):
        """@file:../etc/passwd must be blocked — path traversal."""
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("check @file:../etc/passwd", workspace)
        assert "Access denied" in result or "File not found" in result

    def test_file_absolute_outside_workspace_blocked(self, workspace):
        """@file:/etc/passwd must be blocked — outside workspace."""
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("check @file:/etc/passwd", workspace)
        assert "Access denied" in result or "File not found" in result

    def test_big_file_truncated(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("check @file:big.txt", workspace)
        assert "first 500 of 600 lines" in result

    def test_multiple_file_mentions(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("compare @file:main.py and @file:utils.py", workspace)
        assert "def main():" in result
        assert "def helper():" in result

    def test_diff_mention(self, workspace):
        from agent.context_providers import expand_context_mentions
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "diff --git a/f b/f\n+added line"
        with patch("agent.context_providers.subprocess.run", return_value=mock_result):
            result = expand_context_mentions("show me @diff", workspace)
        assert "git diff" in result
        assert "+added line" in result

    def test_diff_with_ref(self, workspace):
        from agent.context_providers import expand_context_mentions
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "diff content"
        with patch("agent.context_providers.subprocess.run", return_value=mock_result):
            result = expand_context_mentions("show @diff:HEAD~1", workspace)
        assert "git diff HEAD~1" in result

    def test_diff_ref_injection_blocked(self, workspace):
        """@diff:$(ls) should be rejected — command injection attempt."""
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("show @diff:$(ls)", workspace)
        assert "Invalid git ref" in result

    def test_diff_ref_path_traversal_blocked(self, workspace):
        """@diff:../../etc/passwd should be rejected — path traversal."""
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("show @diff:../../etc/passwd", workspace)
        assert "Invalid git ref" in result

    def test_diff_no_changes(self, workspace):
        from agent.context_providers import expand_context_mentions
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("agent.context_providers.subprocess.run", return_value=mock_result):
            result = expand_context_mentions("@diff", workspace)
        assert "no changes" in result

    def test_codebase_mention(self, workspace):
        from agent.context_providers import expand_context_mentions
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "main.py:1:def main():\n"
        with patch("agent.context_providers.subprocess.run", return_value=mock_result):
            result = expand_context_mentions("find @codebase:main", workspace)
        assert "Codebase search" in result
        assert "def main():" in result

    def test_mentions_stripped_from_prompt(self, workspace):
        from agent.context_providers import expand_context_mentions
        result = expand_context_mentions("fix @file:main.py bug", workspace)
        # The original @file:main.py should be removed from the prompt text
        assert "@file:main.py" not in result.split("---")[0]


class TestContextProviderHook:
    def test_hook_no_mentions(self):
        import asyncio
        from agent.context_providers import context_provider_hook
        result = asyncio.run(context_provider_hook("normal prompt"))
        assert result is None  # passthrough

    def test_hook_with_mentions(self, workspace):
        import asyncio
        from agent.context_providers import context_provider_hook
        with patch("agent.context_providers.config") as mock_config:
            mock_config.WORKSPACE_DIR = workspace
            mock_config.RIPGREP_PATH = "rg"
            result = asyncio.run(context_provider_hook("check @file:main.py", "planner"))
        assert result is not None
        assert "def main():" in result
