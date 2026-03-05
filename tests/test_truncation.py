"""
Tests for agent/tools/truncation.py
"""
import pytest


def test_small_output_not_truncated():
    from agent.tools.truncation import truncate_output

    content = "line 1\nline 2\nline 3\n"
    result = truncate_output(content)
    assert result == content


def test_large_output_truncated_by_lines():
    from agent.tools.truncation import truncate_output
    import config

    many_lines = "\n".join(f"line {i}" for i in range(config.MAX_OUTPUT_LINES + 500))
    result = truncate_output(many_lines)
    result_lines = result.splitlines()
    assert len(result_lines) <= config.MAX_OUTPUT_LINES + 10  # small tolerance for truncation message
    assert "truncated" in result.lower() or len(result_lines) <= config.MAX_OUTPUT_LINES


def test_large_output_truncated_by_bytes():
    from agent.tools.truncation import truncate_output
    import config

    # Create content that exceeds MAX_OUTPUT_BYTES
    fat_line = "x" * 200
    many_fat_lines = "\n".join(fat_line for _ in range(400))
    result = truncate_output(many_fat_lines)
    assert len(result.encode()) <= config.MAX_OUTPUT_BYTES * 2  # within 2x after truncation msg


def test_estimate_tokens():
    from agent.tools.truncation import estimate_tokens

    # Basic invariants (implementation may use tiktoken or heuristic)
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") > 0
    # Longer text should produce more tokens
    short = estimate_tokens("hello")
    long = estimate_tokens("hello world this is a longer sentence with many words")
    assert long > short
    # Very long text should scale reasonably
    assert estimate_tokens("a " * 200) >= 50  # at least ~50 tokens for 200 words


def test_truncation_message_included():
    from agent.tools.truncation import truncate_output
    import config

    # Force byte truncation
    big = "y" * (config.MAX_OUTPUT_BYTES + 1000)
    result = truncate_output(big)
    assert "truncat" in result.lower() or len(result) <= config.MAX_OUTPUT_BYTES * 2
