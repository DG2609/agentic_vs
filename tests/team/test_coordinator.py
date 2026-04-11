"""Tests for coordinator mode detection and system prompt."""
import pytest
import config


def test_coordinator_mode_default_false():
    assert config.COORDINATOR_MODE is False


def test_agent_state_has_coordinator_fields():
    from models.state import AgentState
    state = AgentState()
    assert state.coordinator_mode is False
    assert state.team_notifications == []


def test_coordinator_prompt_contains_key_sections():
    from agent.team.coordinator import get_coordinator_system_prompt
    prompt = get_coordinator_system_prompt()
    assert "coordinator" in prompt.lower()
    assert "worker_spawn" in prompt
    assert "worker_message" in prompt
    assert "team_status" in prompt
    assert "Research" in prompt
    assert "Implementation" in prompt
    assert "Verification" in prompt


def test_coordinator_prompt_contains_never_stop_rule():
    from agent.team.coordinator import get_coordinator_system_prompt
    prompt = get_coordinator_system_prompt()
    assert "retry" in prompt.lower() or "never stop" in prompt.lower()


def test_coordinator_prompt_contains_notification_format():
    from agent.team.coordinator import get_coordinator_system_prompt
    prompt = get_coordinator_system_prompt()
    assert "<task-notification>" in prompt


def test_is_coordinator_mode_respects_config():
    import config
    from agent.team.coordinator import is_coordinator_mode
    original = config.COORDINATOR_MODE
    config.COORDINATOR_MODE = True
    assert is_coordinator_mode() is True
    config.COORDINATOR_MODE = False
    assert is_coordinator_mode() is False
    config.COORDINATOR_MODE = original
