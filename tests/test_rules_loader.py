"""Tests for agent.rules_loader — hierarchical CLAUDE.md / rules loader."""
import importlib
import sys
from pathlib import Path

import pytest


def _reload_module():
    """Reload the module to clear lru_cache between tests."""
    mod_name = "agent.rules_loader"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure lru_cache is cleared before every test."""
    mod = _reload_module()
    mod._cached_rules.cache_clear()
    yield
    mod._cached_rules.cache_clear()


# ─────────────────────────────────────────────────────────────
# test_no_rules_files
# ─────────────────────────────────────────────────────────────
def test_no_rules_files(tmp_path, monkeypatch):
    """Returns empty string when no rules files exist."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "fake_home"))

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(tmp_path / "project"))
    assert result == ""


# ─────────────────────────────────────────────────────────────
# test_project_claude_md
# ─────────────────────────────────────────────────────────────
def test_project_claude_md(tmp_path, monkeypatch):
    """Reads CLAUDE.md from project root."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "fake_home"))

    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("Always write tests.", encoding="utf-8")

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(project))

    assert "Always write tests." in result
    assert "CLAUDE.md" in result


# ─────────────────────────────────────────────────────────────
# test_hierarchical_merge
# ─────────────────────────────────────────────────────────────
def test_hierarchical_merge(tmp_path, monkeypatch):
    """Global + project + local all merged in order (lowest → highest priority)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    # 1. Global user rules
    (fake_home / ".shadowdev").mkdir()
    (fake_home / ".shadowdev" / "CLAUDE.md").write_text("GLOBAL RULE", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()

    # 2. Project-root rules
    (project / "CLAUDE.md").write_text("PROJECT RULE", encoding="utf-8")

    # 3. Project-local rules
    (project / ".shadowdev").mkdir()
    (project / ".shadowdev" / "CLAUDE.md").write_text("LOCAL RULE", encoding="utf-8")

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(project))

    # All three must be present
    assert "GLOBAL RULE" in result
    assert "PROJECT RULE" in result
    assert "LOCAL RULE" in result

    # Order: global first, local last
    assert result.index("GLOBAL RULE") < result.index("PROJECT RULE")
    assert result.index("PROJECT RULE") < result.index("LOCAL RULE")


# ─────────────────────────────────────────────────────────────
# test_rules_dir_sorted
# ─────────────────────────────────────────────────────────────
def test_rules_dir_sorted(tmp_path, monkeypatch):
    """rules/*.md files are included and sorted by filename."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "fake_home"))

    project = tmp_path / "project"
    rules_dir = project / ".shadowdev" / "rules"
    rules_dir.mkdir(parents=True)

    (rules_dir / "z-last.md").write_text("LAST RULE", encoding="utf-8")
    (rules_dir / "a-first.md").write_text("FIRST RULE", encoding="utf-8")
    (rules_dir / "m-middle.md").write_text("MIDDLE RULE", encoding="utf-8")

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(project))

    assert "FIRST RULE" in result
    assert "MIDDLE RULE" in result
    assert "LAST RULE" in result

    # Alphabetical order: a-first < m-middle < z-last
    assert result.index("FIRST RULE") < result.index("MIDDLE RULE")
    assert result.index("MIDDLE RULE") < result.index("LAST RULE")


# ─────────────────────────────────────────────────────────────
# test_empty_file_skipped
# ─────────────────────────────────────────────────────────────
def test_empty_file_skipped(tmp_path, monkeypatch):
    """Empty files are not included in the output."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "fake_home"))

    project = tmp_path / "project"
    project.mkdir()

    # Empty project CLAUDE.md
    (project / "CLAUDE.md").write_text("", encoding="utf-8")

    # Rules dir with one empty and one non-empty file
    rules_dir = project / ".shadowdev" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "empty.md").write_text("   \n  ", encoding="utf-8")
    (rules_dir / "valid.md").write_text("VALID RULE", encoding="utf-8")

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(project))

    assert "VALID RULE" in result
    # Empty CLAUDE.md should contribute no section
    assert result.count("## Rules from CLAUDE.md") == 0
    # Empty rules file should be skipped too
    assert "empty.md" not in result


# ─────────────────────────────────────────────────────────────
# test_rules_after_project_claude_md (ordering: rules/ comes after local CLAUDE.md)
# ─────────────────────────────────────────────────────────────
def test_rules_dir_after_local_claude_md(tmp_path, monkeypatch):
    """rules/ dir entries come after .shadowdev/CLAUDE.md in the merged output."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "fake_home"))

    project = tmp_path / "project"
    shadowdev = project / ".shadowdev"
    rules_dir = shadowdev / "rules"
    rules_dir.mkdir(parents=True)

    (shadowdev / "CLAUDE.md").write_text("LOCAL OVERRIDE", encoding="utf-8")
    (rules_dir / "extra.md").write_text("EXTRA RULE", encoding="utf-8")

    from agent.rules_loader import load_project_rules
    result = load_project_rules(workspace=str(project))

    assert result.index("LOCAL OVERRIDE") < result.index("EXTRA RULE")
