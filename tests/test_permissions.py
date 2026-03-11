"""Tests for agent/permissions.py — per-tool permission system."""
import os
import sqlite3
from unittest.mock import patch, AsyncMock

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Use temp DB for permissions during tests."""
    db_path = str(tmp_path / "permissions.db")
    with patch("agent.permissions._DB_PATH", db_path):
        yield db_path


class TestSaveAndGetPermission:
    def test_default_read_tool_allowed(self, tmp_db):
        from agent.permissions import get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            assert get_decision("file_read") == "allow"
            assert get_decision("code_search") == "allow"

    def test_default_write_tool_asks(self, tmp_db):
        from agent.permissions import get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            assert get_decision("file_edit") == "ask"
            assert get_decision("terminal_exec") == "ask"
            assert get_decision("git_commit") == "ask"

    def test_save_allow_overrides_default(self, tmp_db):
        from agent.permissions import save_permission, get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("file_edit", "allow")
            assert get_decision("file_edit") == "allow"

    def test_save_deny(self, tmp_db):
        from agent.permissions import save_permission, get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("terminal_exec", "deny")
            assert get_decision("terminal_exec") == "deny"

    def test_glob_pattern(self, tmp_db):
        from agent.permissions import save_permission, get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("git_*", "allow")
            assert get_decision("git_commit") == "allow"
            assert get_decision("git_push") == "allow"

    def test_file_pattern(self, tmp_db):
        from agent.permissions import save_permission, get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("file_edit", "deny", file_pattern="*.env")
            assert get_decision("file_edit", ".env") == "deny"
            assert get_decision("file_edit", "main.py") == "ask"  # default for write tool

    def test_most_recent_rule_wins(self, tmp_db):
        from agent.permissions import save_permission, get_decision
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("file_edit", "allow")
            save_permission("file_edit", "deny")
            assert get_decision("file_edit") == "deny"

    def test_invalid_decision_raises(self, tmp_db):
        from agent.permissions import save_permission
        with patch("agent.permissions._DB_PATH", tmp_db):
            with pytest.raises(ValueError, match="Invalid decision"):
                save_permission("foo", "maybe")


class TestListAndDeletePermissions:
    def test_list_empty(self, tmp_db):
        from agent.permissions import list_permissions
        with patch("agent.permissions._DB_PATH", tmp_db):
            assert list_permissions() == []

    def test_list_after_save(self, tmp_db):
        from agent.permissions import save_permission, list_permissions
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("file_edit", "allow")
            rules = list_permissions()
        assert len(rules) == 1
        assert rules[0]["tool_pattern"] == "file_edit"
        assert rules[0]["decision"] == "allow"

    def test_delete(self, tmp_db):
        from agent.permissions import save_permission, delete_permission, list_permissions
        with patch("agent.permissions._DB_PATH", tmp_db):
            rule_id = save_permission("file_edit", "allow")
            assert delete_permission(rule_id) is True
            assert list_permissions() == []

    def test_delete_nonexistent(self, tmp_db):
        from agent.permissions import delete_permission
        with patch("agent.permissions._DB_PATH", tmp_db):
            assert delete_permission(99999) is False


class TestClearPermissions:
    def test_clear(self, tmp_db):
        from agent.permissions import save_permission, clear_permissions, list_permissions
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("a", "allow")
            save_permission("b", "deny")
            cleared = clear_permissions()
            assert cleared == 2
            assert list_permissions() == []


class TestCheckPermission:
    def test_allow_read_tool(self, tmp_db):
        import asyncio
        from agent.permissions import check_permission
        with patch("agent.permissions._DB_PATH", tmp_db):
            allowed, reason = asyncio.run(check_permission("file_read", {}))
        assert allowed is True

    def test_deny_blocked_tool(self, tmp_db):
        import asyncio
        from agent.permissions import check_permission, save_permission
        with patch("agent.permissions._DB_PATH", tmp_db):
            save_permission("terminal_exec", "deny")
            allowed, reason = asyncio.run(check_permission("terminal_exec", {"command": "rm -rf /"}))
        assert allowed is False
        assert "denied" in reason.lower()

    def test_ask_with_callback_allow(self, tmp_db):
        import asyncio
        from agent.permissions import check_permission, set_permission_callback
        cb = AsyncMock(return_value="allow")
        set_permission_callback(cb)
        try:
            with patch("agent.permissions._DB_PATH", tmp_db):
                allowed, reason = asyncio.run(check_permission("file_edit", {"file_path": "x.py"}))
            assert allowed is True
            cb.assert_called_once()
        finally:
            set_permission_callback(None)

    def test_ask_with_callback_deny(self, tmp_db):
        import asyncio
        from agent.permissions import check_permission, set_permission_callback
        cb = AsyncMock(return_value="deny")
        set_permission_callback(cb)
        try:
            with patch("agent.permissions._DB_PATH", tmp_db):
                allowed, reason = asyncio.run(check_permission("file_edit", {"file_path": "x.py"}))
            assert allowed is False
        finally:
            set_permission_callback(None)

    def test_ask_with_always_allow_saves(self, tmp_db):
        import asyncio
        from agent.permissions import check_permission, set_permission_callback, get_decision
        cb = AsyncMock(return_value="always_allow")
        set_permission_callback(cb)
        try:
            with patch("agent.permissions._DB_PATH", tmp_db):
                allowed, _ = asyncio.run(check_permission("file_edit", {}))
                assert allowed is True
                # Should have saved the rule
                assert get_decision("file_edit") == "allow"
        finally:
            set_permission_callback(None)

    def test_ask_no_callback_defaults_allow(self, tmp_db):
        """In headless/CLI mode with no callback, default to allow."""
        import asyncio
        from agent.permissions import check_permission, set_permission_callback
        set_permission_callback(None)
        with patch("agent.permissions._DB_PATH", tmp_db):
            allowed, _ = asyncio.run(check_permission("file_edit", {}))
        assert allowed is True
