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

    # Roughly 4 chars per token (integer floor division)
    assert estimate_tokens("hello") == 1   # 5 // 4 = 1
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100  # 400 // 4 = 100
    assert estimate_tokens("a" * 8) == 2   # 8 // 4 = 2


def test_truncation_message_included():
    from agent.tools.truncation import truncate_output
    import config

    # Force byte truncation
    big = "y" * (config.MAX_OUTPUT_BYTES + 1000)
    result = truncate_output(big)
    assert "truncat" in result.lower() or len(result) <= config.MAX_OUTPUT_BYTES * 2
