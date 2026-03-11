"""Tests for the extended rules system (.shadowdev/rules/*.md)."""
import os

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with rules files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


class TestLoadProjectRules:
    def test_no_rules(self, workspace):
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        assert result == ""

    def test_agents_md(self, workspace):
        with open(os.path.join(workspace, "AGENTS.md"), "w") as f:
            f.write("# Project Rules\nAlways use type hints.\n")
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        assert "Always use type hints" in result
        assert "Project Rules (AGENTS.md)" in result

    def test_shadowdev_rules_dir(self, workspace):
        rules_dir = os.path.join(workspace, ".shadowdev", "rules")
        os.makedirs(rules_dir)
        with open(os.path.join(rules_dir, "style.md"), "w") as f:
            f.write("Use black formatting.\n")
        with open(os.path.join(rules_dir, "testing.md"), "w") as f:
            f.write("Always write tests.\n")
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        assert "Rule: style" in result
        assert "Use black formatting" in result
        assert "Rule: testing" in result
        assert "Always write tests" in result

    def test_rules_sorted_alphabetically(self, workspace):
        rules_dir = os.path.join(workspace, ".shadowdev", "rules")
        os.makedirs(rules_dir)
        with open(os.path.join(rules_dir, "z-last.md"), "w") as f:
            f.write("last rule\n")
        with open(os.path.join(rules_dir, "a-first.md"), "w") as f:
            f.write("first rule\n")
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        # a-first should appear before z-last
        assert result.index("first rule") < result.index("last rule")

    def test_combined_root_and_dir_rules(self, workspace):
        # Root-level rule
        with open(os.path.join(workspace, "AGENTS.md"), "w") as f:
            f.write("Root rule.\n")
        # Directory rule
        rules_dir = os.path.join(workspace, ".shadowdev", "rules")
        os.makedirs(rules_dir)
        with open(os.path.join(rules_dir, "extra.md"), "w") as f:
            f.write("Directory rule.\n")
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        assert "Root rule" in result
        assert "Directory rule" in result

    def test_non_md_files_ignored(self, workspace):
        rules_dir = os.path.join(workspace, ".shadowdev", "rules")
        os.makedirs(rules_dir)
        with open(os.path.join(rules_dir, "valid.md"), "w") as f:
            f.write("valid\n")
        with open(os.path.join(rules_dir, "ignored.txt"), "w") as f:
            f.write("should not appear\n")
        from agent.nodes import _load_project_rules
        result = _load_project_rules(workspace)
        assert "valid" in result
        assert "should not appear" not in result

    def test_empty_workspace(self):
        from agent.nodes import _load_project_rules
        assert _load_project_rules("") == ""
