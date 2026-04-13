"""
tests/test_markdown_streaming.py

Tests for:
- _split_stable_markdown  (cli.py)
- _supports_synchronized_output (cli.py)
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Import helpers under test
# ---------------------------------------------------------------------------

from cli import _split_stable_markdown, _supports_synchronized_output


# ---------------------------------------------------------------------------
# _split_stable_markdown
# ---------------------------------------------------------------------------

class TestSplitStableMarkdown:

    def test_split_stable_no_block(self):
        """Returns ('', text) when there is no double-newline (no complete block yet)."""
        text = "Hello, this is a single line with no blank lines"
        stable, unstable = _split_stable_markdown(text)
        assert stable == ""
        assert unstable == text

    def test_split_stable_basic(self):
        """Splits at the last double-newline into (stable, unstable)."""
        text = "First paragraph.\n\nSecond paragraph.\n\nIncomplete third"
        stable, unstable = _split_stable_markdown(text)
        # stable should end with the second paragraph's trailing \n\n
        assert stable == "First paragraph.\n\nSecond paragraph.\n\n"
        assert unstable == "Incomplete third"

    def test_split_stable_single_block_boundary(self):
        """Only one double-newline: stable is the text before it, unstable after."""
        text = "Block one.\n\nBlock two in progress"
        stable, unstable = _split_stable_markdown(text)
        assert stable == "Block one.\n\n"
        assert unstable == "Block two in progress"

    def test_split_stable_inside_code_fence(self):
        """Does NOT split inside a triple-backtick code fence."""
        # One opening fence before the double-newline means stable.count('```') is odd
        # → should back off to the previous safe break.
        text = "Intro\n\n```python\ncode here\n\nstill inside fence"
        stable, unstable = _split_stable_markdown(text)
        # "Intro\n\n" has even backtick count (0), so split retreats to before ```python
        assert stable == "Intro\n\n"
        assert unstable == "```python\ncode here\n\nstill inside fence"

    def test_split_stable_code_fence_closed(self):
        """Splits normally when code fence is properly closed (even backtick count)."""
        text = "Before\n\n```python\ncode\n```\n\nAfter partial"
        stable, unstable = _split_stable_markdown(text)
        # Two ``` → even count, safe to split at last \n\n
        assert stable == "Before\n\n```python\ncode\n```\n\n"
        assert unstable == "After partial"

    def test_split_stable_inside_fence_no_prev_break(self):
        """Falls back to ('', text) when inside fence with no prior safe break."""
        text = "```python\ncode\n\nmore code still inside fence"
        stable, unstable = _split_stable_markdown(text)
        # stable.count('```') == 1 (odd), no previous \n\n before the fence
        assert stable == ""
        assert unstable == text

    def test_split_stable_empty_string(self):
        """Empty string yields ('', '')."""
        stable, unstable = _split_stable_markdown("")
        assert stable == ""
        assert unstable == ""

    def test_split_stable_only_newlines(self):
        """String of only double-newlines: stable ends at last \n\n, unstable is empty."""
        text = "a\n\nb\n\n"
        stable, unstable = _split_stable_markdown(text)
        assert stable == "a\n\nb\n\n"
        assert unstable == ""


# ---------------------------------------------------------------------------
# _supports_synchronized_output
# ---------------------------------------------------------------------------

class TestSupportsSynchronizedOutput:

    def test_returns_false_for_tmux(self):
        """Returns False when TMUX env var contains 'tmux' (chunked rendering)."""
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1234/default,12345,0",
                                      "TERM_PROGRAM": "ghostty"}, clear=False):
            assert _supports_synchronized_output() is False

    def test_returns_true_for_ghostty(self):
        """Returns True for Ghostty terminal."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "ghostty", "TMUX": "",
                                      "TERM": ""}, clear=False):
            assert _supports_synchronized_output() is True

    def test_returns_true_for_iterm2(self):
        """Returns True for iTerm2."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app", "TMUX": "",
                                      "TERM": ""}, clear=False):
            assert _supports_synchronized_output() is True

    def test_returns_true_for_wezterm(self):
        """Returns True for WezTerm."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "WezTerm", "TMUX": "",
                                      "TERM": ""}, clear=False):
            assert _supports_synchronized_output() is True

    def test_returns_true_for_kitty(self):
        """Returns True when TERM_PROGRAM is 'kitty'."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "kitty", "TMUX": "",
                                      "TERM": ""}, clear=False):
            assert _supports_synchronized_output() is True

    def test_returns_true_for_kitty_in_term(self):
        """Returns True when 'kitty' appears in TERM env var."""
        with patch.dict(os.environ, {"TERM": "xterm-kitty", "TERM_PROGRAM": "",
                                      "TMUX": ""}, clear=False):
            assert _supports_synchronized_output() is True

    def test_returns_false_for_xterm(self):
        """Returns False for plain xterm (no BSU/ESU support)."""
        with patch.dict(os.environ, {"TERM": "xterm-256color", "TERM_PROGRAM": "",
                                      "TMUX": ""}, clear=False):
            assert _supports_synchronized_output() is False

    def test_returns_false_for_unknown(self):
        """Returns False when TERM and TERM_PROGRAM are empty."""
        with patch.dict(os.environ, {"TERM": "", "TERM_PROGRAM": "", "TMUX": ""},
                        clear=False):
            assert _supports_synchronized_output() is False
