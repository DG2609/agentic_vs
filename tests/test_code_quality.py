"""
Tests for agent/tools/code_quality.py
"""
import os
import pytest


def test_python_analysis_returns_report(sample_py_file):
    from agent.tools.code_quality import code_quality

    result = code_quality.invoke({"file_path": "sample.py", "include_todos": True})
    assert "Code Quality Report" in result
    assert "Python" in result
    assert "Functions" in result
    assert "TODO" in result or "No issues" in result


def test_python_complexity_detection(workspace):
    from agent.tools.code_quality import code_quality
    from agent.tools.file_ops import file_write

    # Write a deeply complex function
    code = '''
def mega_complex(x, a, b, c, d, e, f, g):
    if x > 0:
        if a:
            for i in range(10):
                while b:
                    if c and d:
                        try:
                            if e or f:
                                pass
                        except Exception:
                            pass
    return x
'''
    file_write.invoke({"file_path": "complex.py", "content": code})
    result = code_quality.invoke({"file_path": "complex.py"})
    assert "complexity" in result.lower() or "Complexity" in result


def test_python_todo_detection(workspace):
    from agent.tools.code_quality import code_quality
    from agent.tools.file_ops import file_write

    code = '''
def foo():
    # TODO: fix this
    # FIXME: broken
    # HACK: workaround
    pass
'''
    file_write.invoke({"file_path": "todos.py", "content": code})
    result = code_quality.invoke({"file_path": "todos.py", "include_todos": True})
    assert "TODO" in result or "FIXME" in result or "HACK" in result


def test_python_no_todos_when_disabled(workspace):
    from agent.tools.code_quality import code_quality
    from agent.tools.file_ops import file_write

    code = '# TODO: this should not appear\ndef foo(): pass\n'
    file_write.invoke({"file_path": "no_todo.py", "content": code})
    result = code_quality.invoke({"file_path": "no_todo.py", "include_todos": False})
    # TODOs section should not appear when disabled
    lines = result.splitlines()
    todo_lines = [l for l in lines if "TODO" in l and "L" in l and ":" in l]
    assert len(todo_lines) == 0


def test_js_analysis(sample_js_file):
    from agent.tools.code_quality import code_quality

    result = code_quality.invoke({"file_path": "sample.js", "include_todos": True})
    assert "Code Quality Report" in result
    assert "JS" in result
    assert "Functions" in result
    # Should detect functions: greet, fetchData
    assert "greet" in result or "fetchData" in result
    # Should detect TODO
    assert "TODO" in result


def test_nonexistent_file_returns_error():
    from agent.tools.code_quality import code_quality

    result = code_quality.invoke({"file_path": "this_file_does_not_exist.py"})
    assert "not found" in result.lower() or "error" in result.lower()


def test_unknown_extension_returns_basic_stats(workspace):
    from agent.tools.code_quality import code_quality
    from agent.tools.file_ops import file_write

    file_write.invoke({"file_path": "data.xyz", "content": "some content\nmore content\n"})
    result = code_quality.invoke({"file_path": "data.xyz"})
    assert "Lines" in result or "lines" in result.lower()


def test_syntax_error_file_reports_gracefully(workspace):
    from agent.tools.code_quality import code_quality
    from agent.tools.file_ops import file_write

    file_write.invoke({"file_path": "broken.py", "content": "def foo(:\n    pass\n"})
    result = code_quality.invoke({"file_path": "broken.py"})
    assert "syntax" in result.lower() or "error" in result.lower()
