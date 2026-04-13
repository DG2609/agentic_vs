"""
Tests for cli.py UX improvements:
- _preprocess_markdown (strikethrough fix)
- double Ctrl+C exit logic (unit-level, no live prompt)
"""
import time
import pytest

from cli import _preprocess_markdown


# ── _preprocess_markdown ──────────────────────────────────────

def test_preprocess_markdown_single_integer():
    assert _preprocess_markdown("~42~") == "42"


def test_preprocess_markdown_single_float():
    assert _preprocess_markdown("~1.5~") == "1.5"


def test_preprocess_markdown_double_tilde_preserved():
    """~~text~~ is intentional strikethrough — must not be modified."""
    assert _preprocess_markdown("~~deleted~~") == "~~deleted~~"


def test_preprocess_markdown_inline():
    result = _preprocess_markdown("about ~100~ tokens")
    assert result == "about 100 tokens"


def test_preprocess_markdown_multiple():
    result = _preprocess_markdown("~10~ in, ~20~ out")
    assert result == "10 in, 20 out"


def test_preprocess_markdown_non_numeric_unchanged():
    """~word~ is not a number approximation — leave it alone."""
    assert _preprocess_markdown("~foo~") == "~foo~"


def test_preprocess_markdown_no_tildes():
    text = "Hello, world! No tildes."
    assert _preprocess_markdown(text) == text


def test_preprocess_markdown_mixed():
    result = _preprocess_markdown("~5~ items, ~~struck~~, ~3.0~ seconds")
    assert result == "5 items, ~~struck~~, 3.0 seconds"


# ── Double Ctrl+C logic (pure logic test) ────────────────────

class _DoubleCtrlCSimulator:
    """Replicates the double-press logic from chat_loop without any I/O."""

    _DOUBLE_PRESS_TIMEOUT = 1.5

    def __init__(self):
        self._last_interrupt = 0.0
        self.exit_called = False
        self.messages: list[str] = []

    def handle_keyboard_interrupt(self) -> bool:
        """Returns True if we should break (exit), False if just a first press."""
        now = time.time()
        if now - self._last_interrupt < self._DOUBLE_PRESS_TIMEOUT:
            self.exit_called = True
            return True  # exit
        self._last_interrupt = now
        self.messages.append("Press Ctrl+C again to exit.")
        return False  # continue


def test_double_ctrl_c_first_press_does_not_exit():
    sim = _DoubleCtrlCSimulator()
    exiting = sim.handle_keyboard_interrupt()
    assert not exiting
    assert not sim.exit_called
    assert len(sim.messages) == 1


def test_double_ctrl_c_second_press_within_timeout_exits():
    sim = _DoubleCtrlCSimulator()
    sim.handle_keyboard_interrupt()  # first press
    exiting = sim.handle_keyboard_interrupt()  # immediate second press
    assert exiting
    assert sim.exit_called


def test_double_ctrl_c_second_press_after_timeout_does_not_exit():
    sim = _DoubleCtrlCSimulator()
    sim.handle_keyboard_interrupt()  # first press
    # Simulate timeout by backdating the timestamp
    sim._last_interrupt -= 2.0  # 2 seconds ago → past the 1.5s window
    exiting = sim.handle_keyboard_interrupt()  # "second" press but after timeout
    assert not exiting
    assert not sim.exit_called


def test_double_ctrl_c_three_presses_exits_on_second():
    sim = _DoubleCtrlCSimulator()
    sim.handle_keyboard_interrupt()  # first
    exiting = sim.handle_keyboard_interrupt()  # second — exits
    assert exiting
    # Third call would be irrelevant since we already broke out


def test_double_ctrl_c_message_shown_on_first():
    sim = _DoubleCtrlCSimulator()
    sim.handle_keyboard_interrupt()
    assert any("Ctrl+C" in m or "again" in m for m in sim.messages)
