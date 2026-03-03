"""
Tests for agent/tools/code_search.py
"""
import os
import pytest


def test_grep_search_finds_pattern(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.code_search import grep_search

    file_write.invoke({"file_path": "search_me.py", "content": "def authenticate(token): pass\ndef login(): pass\n"})

    result = grep_search.invoke({"keyword": "authenticate", "file_path": "search_me.py"})
    assert "authenticate" in result


def test_grep_search_no_match(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.code_search import grep_search

    file_write.invoke({"file_path": "empty_match.py", "content": "x = 1\n"})
    result = grep_search.invoke({"keyword": "ZZZNOMATCH", "file_path": "empty_match.py"})
    assert "no match" in result.lower() or "not found" in result.lower() or "0" in result


def test_code_search_across_files(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.code_search import code_search

    file_write.invoke({"file_path": "module_a.py", "content": "def shared_function(): pass\n"})
    file_write.invoke({"file_path": "module_b.py", "content": "# uses shared_function\nshared_function()\n"})

    result = code_search.invoke({"query": "shared_function", "max_results": 10})
    assert "shared_function" in result
    assert "module_a" in result or "module_b" in result


def test_batch_read_multiple_files(workspace):
    from agent.tools.file_ops import file_write
    from agent.tools.code_search import batch_read

    file_write.invoke({"file_path": "batch1.py", "content": "# batch1 content\n"})
    file_write.invoke({"file_path": "batch2.py", "content": "# batch2 content\n"})

    result = batch_read.invoke({"file_paths": ["batch1.py", "batch2.py"]})
    assert "batch1 content" in result
    assert "batch2 content" in result


def test_batch_read_handles_missing(workspace):
    from agent.tools.code_search import batch_read

    result = batch_read.invoke({"file_paths": ["nonexistent_xyz.py"]})
    # Should report error but not crash
    assert isinstance(result, str)
    assert len(result) > 0
