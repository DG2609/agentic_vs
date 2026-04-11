# tests/team/test_scratchpad.py
"""Tests for shared team scratchpad."""
import os
import pytest


@pytest.fixture
def pad(tmp_path):
    from agent.team.scratchpad import Scratchpad
    return Scratchpad(root=str(tmp_path / "scratchpad"))


def test_write_and_read(pad):
    pad.write("findings.md", "# Auth bug\nNPE at validate.py:42")
    content = pad.read("findings.md")
    assert "validate.py:42" in content


def test_read_missing_returns_empty(pad):
    assert pad.read("nonexistent.md") == ""


def test_list_files_empty(pad):
    assert pad.list_files() == []


def test_list_files_after_write(pad):
    pad.write("a.md", "hello")
    pad.write("b.md", "world")
    files = pad.list_files()
    assert "a.md" in files
    assert "b.md" in files


def test_write_creates_dir_automatically(tmp_path):
    from agent.team.scratchpad import Scratchpad
    deep = str(tmp_path / "deep" / "nested" / "scratchpad")
    pad = Scratchpad(root=deep)
    pad.write("test.txt", "content")
    assert pad.read("test.txt") == "content"
