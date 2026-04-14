"""Tests for the config viewer/editor tools."""
import pytest
from unittest.mock import patch, MagicMock


# ── config_get ───────────────────────────────────────────────────────────────

def test_config_get_known_key():
    from agent.tools.config_tool import config_get
    import config
    result = config_get.invoke({"key": "ADVISOR_MODEL"})
    assert "ADVISOR_MODEL" in result


def test_config_get_empty_key_shows_all():
    from agent.tools.config_tool import config_get
    result = config_get.invoke({"key": ""})
    assert "ADVISOR_MODEL" in result
    assert "UNDERCOVER_MODE" in result
    assert "Read-only" in result


def test_config_get_unknown_key():
    from agent.tools.config_tool import config_get
    result = config_get.invoke({"key": "NONEXISTENT_KEY_ZZZ"})
    assert "not found" in result.lower() or "<not found>" in result


def test_config_get_shows_mutable_note():
    from agent.tools.config_tool import config_get
    result = config_get.invoke({"key": "ADVISOR_MODEL"})
    assert "mutable" in result.lower() or "config_set" in result


def test_config_get_shows_readonly_note():
    from agent.tools.config_tool import config_get
    result = config_get.invoke({"key": "LLM_PROVIDER"})
    assert "read-only" in result.lower()


# ── config_set ───────────────────────────────────────────────────────────────

def test_config_set_bool_true():
    from agent.tools.config_tool import config_set
    import config
    original = config.UNDERCOVER_MODE
    result = config_set.invoke({"key": "UNDERCOVER_MODE", "value": "true"})
    assert "true" in result.lower() or "True" in result
    config.UNDERCOVER_MODE = original  # restore


def test_config_set_bool_false():
    from agent.tools.config_tool import config_set
    import config
    result = config_set.invoke({"key": "UNDERCOVER_MODE", "value": "false"})
    assert "False" in result or "false" in result
    config.UNDERCOVER_MODE = False  # restore


def test_config_set_string():
    from agent.tools.config_tool import config_set
    import config
    original = config.ADVISOR_MODEL
    config_set.invoke({"key": "ADVISOR_MODEL", "value": "claude-haiku-4-5"})
    assert config.ADVISOR_MODEL == "claude-haiku-4-5"
    config.ADVISOR_MODEL = original  # restore


def test_config_set_invalid_key_rejected():
    from agent.tools.config_tool import config_set
    result = config_set.invoke({"key": "WORKSPACE_DIR", "value": "/evil/path"})
    assert "not a mutable setting" in result.lower() or "Mutable settings" in result


def test_config_set_invalid_choice_rejected():
    from agent.tools.config_tool import config_set
    import config
    original = config.REASONING_EFFORT
    result = config_set.invoke({"key": "REASONING_EFFORT", "value": "extreme"})
    assert "Invalid" in result or "choices" in result.lower()
    config.REASONING_EFFORT = original  # restore


def test_config_set_valid_reasoning_effort():
    from agent.tools.config_tool import config_set
    import config
    original = config.REASONING_EFFORT
    config_set.invoke({"key": "REASONING_EFFORT", "value": "high"})
    assert config.REASONING_EFFORT == "high"
    config.REASONING_EFFORT = original  # restore


def test_config_set_shows_old_and_new():
    from agent.tools.config_tool import config_set
    import config
    original = config.NOTIFY_ON_COMPLETE
    result = config_set.invoke({"key": "NOTIFY_ON_COMPLETE", "value": "false"})
    assert "→" in result or "updated" in result.lower()
    config.NOTIFY_ON_COMPLETE = original  # restore


# ── config_list ──────────────────────────────────────────────────────────────

def test_config_list_shows_settings():
    from agent.tools.config_tool import config_list
    result = config_list.invoke({})
    assert "ADVISOR_MODEL" in result
    assert "UNDERCOVER_MODE" in result


def test_config_set_advisor_model_calls_set_advisor_model():
    from agent.tools.config_tool import config_set
    import config
    original = config.ADVISOR_MODEL
    with patch("agent.advisor.set_advisor_model") as mock_set:
        # Import triggers the advisor module
        config_set.invoke({"key": "ADVISOR_MODEL", "value": "claude-opus-4-6"})
    config.ADVISOR_MODEL = original  # restore
