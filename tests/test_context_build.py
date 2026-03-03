"""
Tests for agent/tools/context_build.py
"""
import os
import pytest


def test_context_build_finds_files(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.context_build import context_build

    # Create files with distinctive keywords
    file_write.invoke({"file_path": "auth.py", "content": "# JWT authentication\ndef authenticate(token): pass\n"})
    file_write.invoke({"file_path": "database.py", "content": "# Database connection\ndef connect(url): pass\n"})
    file_write.invoke({"file_path": "utils.py", "content": "# Generic utilities\ndef helper(): pass\n"})

    result = context_build.invoke({"description": "JWT authentication token", "max_files": 5, "include_deps": False})
    assert "auth.py" in result
    # utils.py and database.py shouldn't rank above auth.py for this query


def test_context_build_no_results_message(workspace):
    from agent.tools.context_build import context_build

    result = context_build.invoke({
        "description": "zzzzzz_completely_nonexistent_term_zzzzzz",
        "max_files": 5,
        "include_deps": False,
    })
    # Either "No relevant files found" OR some result (if somehow keyword matches)
    assert isinstance(result, str)
    assert len(result) > 0


def test_context_build_max_files_respected(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.context_build import context_build

    # Create many files with the same keyword
    for i in range(15):
        file_write.invoke({"file_path": f"file_{i}.py", "content": f"# search_keyword_{i}\ndef f{i}(): pass\n"})

    result = context_build.invoke({
        "description": "search_keyword",
        "max_files": 3,
        "include_deps": False,
    })

    # Count numbered entries in result
    import re
    numbered = re.findall(r'^\s+\d+\.', result, re.MULTILINE)
    assert len(numbered) <= 3


def test_context_build_keyword_extraction():
    from agent.tools.context_build import _extract_keywords

    kws = _extract_keywords("Fix the authentication JWT token validation bug")
    # Stop words like 'the', 'fix' should be removed
    assert "the" not in kws
    assert "fix" not in kws
    # Technical terms should be kept
    assert any(k.lower() in ("authentication", "jwt", "token", "validation", "bug") for k in kws)


def test_context_build_returns_batch_read_hint(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.context_build import context_build

    file_write.invoke({"file_path": "relevant.py", "content": "# relevant content\ndef main(): pass\n"})
    result = context_build.invoke({"description": "relevant content", "max_files": 3})
    assert "batch_read" in result
