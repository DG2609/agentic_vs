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
