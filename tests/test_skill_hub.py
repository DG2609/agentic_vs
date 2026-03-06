"""
Tests for agent/skill_hub.py and the skill hub tool wrappers
(hub_search, skill_install, skill_remove) in agent/tools/skills.py.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.skill_hub import (
    HubIndex,
    _validate_url,
    _download,
    install_skill,
    remove_skill,
    list_installed,
)


# ── Fixtures ──────────────────────────────────────────────────

SAMPLE_INDEX = {
    "version": 1,
    "skills": [
        {
            "name": "deploy-fly",
            "description": "Deploy project to Fly.io",
            "category": "devops",
            "tags": ["deploy", "flyio", "cloud"],
            "version": "1.0.0",
            "author": "shadowdev",
            "url": "https://raw.githubusercontent.com/test/skills/deploy-fly.md",
            "type": "markdown",
        },
        {
            "name": "security-scan",
            "description": "Run OWASP security scan",
            "category": "security",
            "tags": ["security", "owasp", "scan"],
            "version": "2.1.0",
            "author": "shadowdev",
            "url": "https://raw.githubusercontent.com/test/skills/security-scan.md",
            "type": "markdown",
        },
        {
            "name": "lint-plugin",
            "description": "Custom linting plugin",
            "category": "devops",
            "tags": ["lint", "quality"],
            "version": "0.5.0",
            "author": "contrib",
            "url": "https://raw.githubusercontent.com/test/plugins/lint-plugin.py",
            "type": "plugin",
        },
    ],
}


@pytest.fixture
def hub_index():
    return HubIndex(SAMPLE_INDEX)


@pytest.fixture
def tmp_skills_dir(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir


# ── HubIndex tests ────────────────────────────────────────────

class TestHubIndex:
    def test_skills_returns_all(self, hub_index):
        assert len(hub_index.skills) == 3

    def test_get_by_name_exact(self, hub_index):
        s = hub_index.get("deploy-fly")
        assert s is not None
        assert s["name"] == "deploy-fly"

    def test_get_by_name_case_insensitive(self, hub_index):
        s = hub_index.get("DEPLOY-FLY")
        assert s is not None
        assert s["name"] == "deploy-fly"

    def test_get_missing_returns_none(self, hub_index):
        assert hub_index.get("nonexistent") is None

    def test_search_by_query_name(self, hub_index):
        results = hub_index.search(query="deploy")
        assert len(results) == 1
        assert results[0]["name"] == "deploy-fly"

    def test_search_by_query_description(self, hub_index):
        results = hub_index.search(query="OWASP")
        assert len(results) == 1
        assert results[0]["name"] == "security-scan"

    def test_search_by_query_tag(self, hub_index):
        results = hub_index.search(query="cloud")
        assert len(results) == 1
        assert results[0]["name"] == "deploy-fly"

    def test_search_by_category(self, hub_index):
        results = hub_index.search(category="devops")
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"deploy-fly", "lint-plugin"}

    def test_search_by_tag(self, hub_index):
        results = hub_index.search(tag="security")
        assert len(results) == 1
        assert results[0]["name"] == "security-scan"

    def test_search_combined_filters(self, hub_index):
        results = hub_index.search(query="plugin", category="devops")
        assert len(results) == 1
        assert results[0]["name"] == "lint-plugin"

    def test_search_empty_returns_all(self, hub_index):
        results = hub_index.search()
        assert len(results) == 3

    def test_search_no_match(self, hub_index):
        results = hub_index.search(query="zzzmissing")
        assert results == []

    def test_categories_sorted(self, hub_index):
        cats = hub_index.categories
        assert cats == sorted(cats)
        assert "devops" in cats
        assert "security" in cats

    def test_empty_index(self):
        idx = HubIndex({"version": 1, "skills": []})
        assert idx.skills == []
        assert idx.categories == []
        assert idx.search(query="anything") == []


# ── _validate_url tests ───────────────────────────────────────

class TestValidateUrl:
    def test_https_ok(self):
        _validate_url("https://example.com/skill.md")  # should not raise

    def test_http_ok(self):
        _validate_url("http://example.com/skill.md")  # should not raise

    def test_file_scheme_raises(self):
        with pytest.raises(ValueError, match="Only http/https"):
            _validate_url("file:///etc/passwd")

    def test_no_host_raises(self):
        with pytest.raises(ValueError, match="no host"):
            _validate_url("https:///path/only")


# ── _download tests ───────────────────────────────────────────

class TestDownload:
    def test_download_returns_bytes(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b"hello ", b"world"]

        with patch("agent.skill_hub.requests.get", return_value=mock_resp):
            result = _download("https://example.com/skill.md")

        assert result == b"hello world"

    def test_download_enforces_size_limit(self):
        big_chunk = b"x" * (600 * 1024)  # 600KB > 512KB limit

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [big_chunk]

        with patch("agent.skill_hub.requests.get", return_value=mock_resp):
            with pytest.raises(ValueError, match="size limit"):
                _download("https://example.com/big.md")

    def test_download_network_error_raises_runtime(self):
        import requests as req
        with patch("agent.skill_hub.requests.get", side_effect=req.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Download failed"):
                _download("https://example.com/skill.md")


# ── install_skill tests ───────────────────────────────────────

MD_CONTENT = b"---\nname: test-skill\ndescription: Test\n---\n\n# Body"
PLUGIN_CONTENT = b"__skill_tools__ = []\ndef hello(): pass\n"


class TestInstallSkill:
    def test_install_markdown_from_url(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=MD_CONTENT):

            result = install_skill("test-skill", url="https://example.com/test-skill.md")

        assert result["name"] == "test-skill"
        assert result["type"] == "markdown"
        assert result["status"] == "installed"
        assert (tmp_skills_dir / "test-skill.md").exists()

    def test_install_plugin_from_url(self, tmp_skills_dir):
        tools_dir = tmp_skills_dir / "_tools"
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tools_dir), \
             patch("agent.skill_hub._download", return_value=PLUGIN_CONTENT):

            result = install_skill("lint-plugin", url="https://example.com/lint-plugin.py")

        assert result["type"] == "plugin"
        assert (tools_dir / "lint-plugin.py").exists()

    def test_install_already_exists_no_overwrite_raises(self, tmp_skills_dir):
        (tmp_skills_dir / "existing.md").write_bytes(MD_CONTENT)

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=MD_CONTENT):

            with pytest.raises(RuntimeError, match="already installed"):
                install_skill("existing", url="https://example.com/existing.md")

    def test_install_overwrite_replaces(self, tmp_skills_dir):
        dest = tmp_skills_dir / "existing.md"
        dest.write_bytes(b"---\nname: existing\n---\n\nOld content")

        new_content = b"---\nname: existing\n---\n\nNew content"
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=new_content):

            result = install_skill("existing", url="https://example.com/existing.md", overwrite=True)

        assert result["status"] == "updated"
        assert dest.read_bytes() == new_content

    def test_install_bad_name_raises(self, tmp_skills_dir):
        with pytest.raises(ValueError, match="Invalid skill name"):
            install_skill("../evil-name", url="https://example.com/x.md")

    def test_install_plugin_missing_skill_tools_raises(self, tmp_skills_dir):
        bad_plugin = b"def hello(): pass\n"  # no __skill_tools__

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=bad_plugin):

            with pytest.raises(ValueError, match="__skill_tools__"):
                install_skill("bad-plugin", url="https://example.com/bad-plugin.py")

    def test_install_unknown_extension_raises(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=b"content"):

            with pytest.raises(ValueError, match="Cannot determine skill type"):
                install_skill("oddfile", url="https://example.com/oddfile.txt")

    def test_install_from_index(self, tmp_skills_dir):
        mock_index = MagicMock()
        mock_index.get.return_value = SAMPLE_INDEX["skills"][0]  # deploy-fly

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub.fetch_index", return_value=mock_index), \
             patch("agent.skill_hub._download", return_value=MD_CONTENT):

            result = install_skill("deploy-fly")

        assert result["name"] == "deploy-fly"
        assert result["version"] == "1.0.0"

    def test_install_not_in_index_raises(self, tmp_skills_dir):
        mock_index = MagicMock()
        mock_index.get.return_value = None

        with patch("agent.skill_hub.fetch_index", return_value=mock_index):
            with pytest.raises(RuntimeError, match="not found in hub"):
                install_skill("nonexistent")


# ── remove_skill tests ────────────────────────────────────────

class TestRemoveSkill:
    def test_remove_markdown(self, tmp_skills_dir):
        md_file = tmp_skills_dir / "my-skill.md"
        md_file.write_bytes(MD_CONTENT)

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            result = remove_skill("my-skill")

        assert not md_file.exists()
        assert result["status"] == "removed"
        assert len(result["removed"]) == 1

    def test_remove_plugin(self, tmp_skills_dir):
        tools_dir = tmp_skills_dir / "_tools"
        tools_dir.mkdir()
        py_file = tools_dir / "my-plugin.py"
        py_file.write_bytes(PLUGIN_CONTENT)

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tools_dir):

            result = remove_skill("my-plugin")

        assert not py_file.exists()
        assert result["status"] == "removed"

    def test_remove_not_found_raises(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            with pytest.raises(RuntimeError, match="not found"):
                remove_skill("ghost-skill")


# ── list_installed tests ──────────────────────────────────────

class TestListInstalled:
    def test_list_empty(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            result = list_installed()

        assert result == []

    def test_list_markdown_skills(self, tmp_skills_dir):
        (tmp_skills_dir / "alpha.md").write_bytes(MD_CONTENT)
        (tmp_skills_dir / "beta.md").write_bytes(MD_CONTENT)
        (tmp_skills_dir / "_internal.md").write_bytes(MD_CONTENT)  # should be skipped

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            result = list_installed()

        names = [r["name"] for r in result]
        assert "alpha" in names
        assert "beta" in names
        assert "_internal" not in names
        assert all(r["type"] == "markdown" for r in result)

    def test_list_plugins(self, tmp_skills_dir):
        tools_dir = tmp_skills_dir / "_tools"
        tools_dir.mkdir()
        (tools_dir / "my-plugin.py").write_bytes(PLUGIN_CONTENT)
        (tools_dir / "_private.py").write_bytes(PLUGIN_CONTENT)  # should be skipped

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tools_dir):

            result = list_installed()

        plugin_results = [r for r in result if r["type"] == "plugin"]
        names = [r["name"] for r in plugin_results]
        assert "my-plugin" in names
        assert "_private" not in names


# ── Tool wrapper tests ────────────────────────────────────────

class TestHubSearchTool:
    def test_hub_search_returns_results(self, hub_index):
        with patch("agent.skill_hub.fetch_index", return_value=hub_index):
            from agent.tools.skills import hub_search
            result = hub_search.invoke({"query": "deploy"})

        assert "deploy-fly" in result
        assert "skill_install" in result

    def test_hub_search_no_results(self, hub_index):
        with patch("agent.skill_hub.fetch_index", return_value=hub_index):
            from agent.tools.skills import hub_search
            result = hub_search.invoke({"query": "zzznomatch"})

        assert "No hub skills found" in result

    def test_hub_search_fetch_error(self):
        with patch("agent.skill_hub.fetch_index", side_effect=RuntimeError("network down")):
            from agent.tools.skills import hub_search
            result = hub_search.invoke({})

        assert "Hub error" in result
        assert "network down" in result

    def test_hub_search_lists_all_on_empty_query(self, hub_index):
        with patch("agent.skill_hub.fetch_index", return_value=hub_index):
            from agent.tools.skills import hub_search
            result = hub_search.invoke({})

        assert "3 result" in result


class TestSkillInstallTool:
    def test_skill_install_success(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"), \
             patch("agent.skill_hub._download", return_value=MD_CONTENT):

            from agent.tools.skills import skill_install
            result = skill_install.invoke({
                "name": "my-skill",
                "url": "https://example.com/my-skill.md",
            })

        assert "installed" in result
        assert "my-skill" in result

    def test_skill_install_failure(self):
        with patch("agent.skill_hub.fetch_index", side_effect=RuntimeError("hub down")):
            from agent.tools.skills import skill_install
            result = skill_install.invoke({"name": "bad-skill"})

        assert "Install failed" in result

    def test_skill_install_plugin_note(self, tmp_skills_dir):
        tools_dir = tmp_skills_dir / "_tools"
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tools_dir), \
             patch("agent.skill_hub._download", return_value=PLUGIN_CONTENT):

            from agent.tools.skills import skill_install
            result = skill_install.invoke({
                "name": "lint-plugin",
                "url": "https://example.com/lint-plugin.py",
            })

        assert "restart" in result.lower()


class TestSkillRemoveTool:
    def test_skill_remove_success(self, tmp_skills_dir):
        (tmp_skills_dir / "bye-skill.md").write_bytes(MD_CONTENT)

        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            from agent.tools.skills import skill_remove
            result = skill_remove.invoke({"name": "bye-skill"})

        assert "removed" in result.lower()
        assert "bye-skill" in result

    def test_skill_remove_not_found(self, tmp_skills_dir):
        with patch("agent.skill_hub.SKILLS_DIR", tmp_skills_dir), \
             patch("agent.skill_hub.TOOLS_DIR", tmp_skills_dir / "_tools"):

            from agent.tools.skills import skill_remove
            result = skill_remove.invoke({"name": "ghost"})

        assert "Remove failed" in result


# ── fetch_index tests ─────────────────────────────────────────

class TestFetchIndex:
    def test_fetch_index_success(self):
        from agent.skill_hub import fetch_index

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = SAMPLE_INDEX

        with patch("agent.skill_hub.requests.get", return_value=mock_resp):
            index = fetch_index()

        assert len(index.skills) == 3

    def test_fetch_index_network_error(self):
        from agent.skill_hub import fetch_index
        import requests as req

        with patch("agent.skill_hub.requests.get", side_effect=req.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Failed to fetch hub index"):
                fetch_index()

    def test_fetch_index_malformed_json(self):
        from agent.skill_hub import fetch_index

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("bad json", "", 0)

        with patch("agent.skill_hub.requests.get", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="malformed"):
                fetch_index()
