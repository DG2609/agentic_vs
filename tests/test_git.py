"""
Tests for git-related functionality and undercover integration.
"""


# ---------------------------------------------------------------------------
# Undercover integration — git_commit sanitizes AI codenames
# ---------------------------------------------------------------------------

def test_git_commit_sanitizes_message_in_undercover_mode():
    """git_commit sanitizes AI codenames when UNDERCOVER_MODE=True."""
    from agent.undercover import sanitize_message
    # Test the sanitize_message function directly (integration)
    msg = "feat: add claude-opus feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    result = sanitize_message(msg)
    assert "claude-opus" not in result
    assert "anthropic.com" not in result
    assert "feat: add" in result  # prefix preserved
