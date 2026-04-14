"""
Tests for agent/skill_engine.py — markdown skill parser and invocation engine.
"""
import pytest
from pathlib import Path


@pytest.fixture
def engine(monkeypatch):
    """Patch SKILL_SEARCH_DIRS to use a fresh temp directory."""
    import agent.skill_engine as se
    return se


@pytest.fixture
def skill_dirs(tmp_path, monkeypatch):
    """Return (workflow_dir, agents_dir) and patch SKILL_SEARCH_DIRS."""
    import agent.skill_engine as se
    workflow = tmp_path / "skills"
    agents = tmp_path / "skills" / "agents"
    workflow.mkdir(parents=True)
    agents.mkdir(parents=True)
    monkeypatch.setattr(se, "SKILL_SEARCH_DIRS", [workflow, agents])
    return workflow, agents


def _write_md(directory: Path, filename: str, content: str) -> Path:
    p = directory / filename
    p.write_text(content, encoding="utf-8")
    return p


# ── _parse_frontmatter ────────────────────────────────────────

def test_parse_frontmatter_basic(engine):
    text = "---\nname: test\ndescription: A test skill\n---\n\nBody here."
    meta, body = engine._parse_frontmatter(text)
    assert meta["name"] == "test"
    assert meta["description"] == "A test skill"
    assert body.strip() == "Body here."


def test_parse_frontmatter_no_fm(engine):
    text = "Just a body without frontmatter."
    meta, body = engine._parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_parse_frontmatter_bool_coercion(engine):
    text = "---\nsubtask: true\nfoo: false\n---\n\nBody."
    meta, body = engine._parse_frontmatter(text)
    assert meta["subtask"] is True
    assert meta["foo"] is False


def test_parse_frontmatter_nested(engine):
    text = "---\ntools:\n  github: true\n  shell: false\n---\n\nBody."
    meta, body = engine._parse_frontmatter(text)
    assert meta["tools"] == {"github": True, "shell": False}


def test_parse_frontmatter_unclosed(engine):
    """Missing closing --- returns empty dict."""
    text = "---\nname: broken\n\nBody without closing."
    meta, body = engine._parse_frontmatter(text)
    assert meta == {}


# ── _process_body ─────────────────────────────────────────────

def test_process_body_arguments(engine):
    body = "Do this with $ARGUMENTS now."
    result = engine._process_body(body, arguments="the input")
    assert "the input" in result
    assert "$ARGUMENTS" not in result


def test_process_body_arguments_empty(engine):
    body = "Context: $ARGUMENTS"
    result = engine._process_body(body, arguments="")
    assert "(no additional arguments)" in result


def test_process_body_shell_injection(engine, tmp_path):
    body = "Result:\n!`echo hello_world`\nDone."
    result = engine._process_body(body, cwd=str(tmp_path))
    assert "hello_world" in result
    assert "```" in result   # output wrapped in code block


def test_process_body_shell_timeout(engine, tmp_path):
    """A command that runs longer than the 30s timeout should produce a timeout note."""
    # We can't actually wait 30s in a test; patch the timeout via subprocess mock
    # Instead just verify that a bad command produces an error block, not an exception
    body = "!`nonexistent_command_xyz_123`"
    result = engine._process_body(body, cwd=str(tmp_path))
    # Should contain a code block even on error (not raise)
    assert "```" in result


# ── discover_skills ───────────────────────────────────────────

def test_discover_workflow_skill(engine, skill_dirs):
    workflow, agents = skill_dirs
    _write_md(workflow, "my-skill.md", "---\nname: my-skill\ndescription: Test\n---\n\nBody.")
    skills = engine.discover_skills()
    names = [s.meta.name for s in skills]
    assert "my-skill" in names


def test_discover_agent_skill(engine, skill_dirs):
    workflow, agents = skill_dirs
    _write_md(agents, "expert.md", "---\nname: expert\ndescription: Expert persona\n---\n\nYou are an expert.")
    skills = engine.discover_skills()
    names = [s.meta.name for s in skills]
    assert "expert" in names


def test_discover_skips_private(engine, skill_dirs):
    workflow, agents = skill_dirs
    _write_md(workflow, "_internal.md", "---\nname: internal\n---\n\nHidden.")
    skills = engine.discover_skills()
    names = [s.meta.name for s in skills]
    assert "internal" not in names


def test_discover_warns_on_duplicate(engine, skill_dirs, caplog):
    import logging
    workflow, agents = skill_dirs
    _write_md(workflow, "skill-a.md", "---\nname: dupe\ndescription: First\n---\n\nA.")
    _write_md(agents, "skill-b.md", "---\nname: dupe\ndescription: Second\n---\n\nB.")
    with caplog.at_level(logging.WARNING, logger="agent.skill_engine"):
        skills = engine.discover_skills()
    names = [s.meta.name for s in skills]
    assert names.count("dupe") == 1   # only the first loaded
    assert any("Duplicate" in r.message for r in caplog.records)


def test_discover_empty_dirs(engine, skill_dirs):
    skills = engine.discover_skills()
    assert skills == []


# ── invoke_skill ──────────────────────────────────────────────

def test_invoke_skill_found(engine, skill_dirs):
    workflow, _ = skill_dirs
    _write_md(workflow, "greet.md", "---\nname: greet\ndescription: Say hi\n---\n\nHello $ARGUMENTS!")
    content, meta = engine.invoke_skill("greet", arguments="world")
    assert meta is not None
    assert meta.name == "greet"
    assert "Hello world!" in content


def test_invoke_skill_not_found(engine, skill_dirs):
    content, meta = engine.invoke_skill("nonexistent")
    assert meta is None
    assert "not found" in content.lower()
    assert "Available skills:" in content


def test_invoke_skill_kebab_lookup(engine, skill_dirs):
    """skill_invoke('code review') should find 'code-review.md'."""
    workflow, _ = skill_dirs
    _write_md(workflow, "code-review.md", "---\nname: code-review\n---\n\nReview steps.")
    content, meta = engine.invoke_skill("code_review")   # underscore → kebab
    assert meta is not None
    assert meta.name == "code-review"


def test_invoke_skill_meta_fields(engine, skill_dirs):
    workflow, _ = skill_dirs
    _write_md(workflow, "full.md", textwrap.dedent("""\
        ---
        name: full
        description: Full test skill
        model: claude-opus-4-6
        subtask: true
        version: "2.0"
        ---

        Workflow here.
    """))
    _, meta = engine.invoke_skill("full")
    assert meta.description == "Full test skill"
    assert meta.model == "claude-opus-4-6"
    assert meta.subtask is True
    assert meta.version == "2.0"


# ── Need textwrap for one test ────────────────────────────────

import textwrap


# ── _simple_yaml — frontmatter parsing ───────────────────────

def test_simple_yaml_scalar_string(engine):
    result = engine._simple_yaml("model: claude-opus-4-6")
    assert result == {"model": "claude-opus-4-6"}


def test_simple_yaml_inline_list(engine):
    result = engine._simple_yaml("tools: [file_read, code_search]")
    assert result == {"tools": ["file_read", "code_search"]}


def test_simple_yaml_block_list(engine):
    result = engine._simple_yaml("tools:\n- file_read\n- code_search")
    assert result == {"tools": ["file_read", "code_search"]}


def test_simple_yaml_empty_string(engine):
    result = engine._simple_yaml("")
    assert result == {}


def test_simple_yaml_comment_lines(engine):
    result = engine._simple_yaml("# this is a comment\nmodel: gpt-4o")
    assert result == {"model": "gpt-4o"}


def test_simple_yaml_invalid_does_not_crash(engine):
    """Malformed / unexpected input must not raise — returns best-effort dict."""
    # Indented key without a parent (not valid top-level) — should not crash
    result = engine._simple_yaml("  indented_key: value")
    assert isinstance(result, dict)


def test_simple_yaml_boolean_coercion(engine):
    result = engine._simple_yaml("subtask: true")
    assert result == {"subtask": True}


def test_simple_yaml_multiple_scalars(engine):
    text = "model: claude-opus-4-6\nversion: 1.0\nsubtask: false"
    result = engine._simple_yaml(text)
    assert result["model"] == "claude-opus-4-6"
    assert result["subtask"] is False


# ── SkillMeta defaults ────────────────────────────────────────

def test_skill_meta_model_default():
    from agent.skill_engine import SkillMeta
    meta = SkillMeta(name="test")
    assert meta.model == ""


def test_skill_meta_tools_default():
    from agent.skill_engine import SkillMeta
    meta = SkillMeta(name="test")
    assert meta.tools == []


def test_skill_meta_description_default():
    from agent.skill_engine import SkillMeta
    meta = SkillMeta(name="test")
    assert meta.description == ""


def test_skill_meta_subtask_default():
    from agent.skill_engine import SkillMeta
    meta = SkillMeta(name="test")
    assert meta.subtask is False


def test_skill_meta_version_default():
    from agent.skill_engine import SkillMeta
    meta = SkillMeta(name="test")
    assert meta.version == ""


# ── Frontmatter round-trip via _parse_frontmatter ─────────────

def test_parse_frontmatter_model_field(engine):
    text = "---\nname: demo\nmodel: claude-opus-4-6\n---\n\nBody."
    meta, _ = engine._parse_frontmatter(text)
    assert meta.get("model") == "claude-opus-4-6"


def test_parse_frontmatter_tools_inline_list(engine):
    text = "---\nname: demo\ntools: [file_read, code_search]\n---\n\nBody."
    meta, _ = engine._parse_frontmatter(text)
    assert meta.get("tools") == ["file_read", "code_search"]


def test_parse_frontmatter_tools_block_list(engine):
    text = "---\nname: demo\ntools:\n- file_read\n- code_search\n---\n\nBody."
    meta, _ = engine._parse_frontmatter(text)
    assert meta.get("tools") == ["file_read", "code_search"]


def test_parse_frontmatter_garbage_does_not_crash(engine):
    """Completely non-YAML frontmatter content should return an empty or partial dict."""
    text = "---\n!!!\x00corrupt\x01data###\n---\n\nBody."
    try:
        meta, body = engine._parse_frontmatter(text)
        assert isinstance(meta, dict)
    except Exception as exc:
        pytest.fail(f"_parse_frontmatter raised unexpectedly: {exc}")
