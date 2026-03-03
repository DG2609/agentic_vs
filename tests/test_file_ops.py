"""
Tests for agent/tools/file_ops.py
"""
import os
import pytest


def test_file_write_and_read(workspace):
    from agent.tools.file_ops import file_write, file_read

    path = "hello.txt"
    content = "Hello, ShadowDev!\nLine 2\n"

    result = file_write.invoke({"file_path": path, "content": content})
    assert "Written" in result or "wrote" in result.lower() or os.path.isfile(os.path.join(workspace, path))

    read_result = file_read.invoke({"file_path": path})
    assert "Hello, ShadowDev!" in read_result


def test_file_write_creates_parent_dir(workspace):
    from agent.tools.file_ops import file_write

    path = "subdir/nested/file.txt"
    result = file_write.invoke({"file_path": path, "content": "nested content"})
    assert os.path.isfile(os.path.join(workspace, path))


def test_file_edit_basic(workspace):
    from agent.tools.file_ops import file_write, file_edit, file_read

    path = "edit_test.py"
    original = "x = 1\ny = 2\nz = x + y\n"
    file_write.invoke({"file_path": path, "content": original})

    result = file_edit.invoke({
        "file_path": path,
        "old_string": "x = 1",
        "new_string": "x = 10",
    })
    assert "success" in result.lower() or "edited" in result.lower() or "applied" in result.lower()

    read = file_read.invoke({"file_path": path})
    assert "x = 10" in read
    assert "y = 2" in read


def test_file_edit_fails_on_missing_string(workspace):
    from agent.tools.file_ops import file_write, file_edit

    path = "no_match.py"
    file_write.invoke({"file_path": path, "content": "a = 1\n"})

    result = file_edit.invoke({
        "file_path": path,
        "old_string": "THIS_DOES_NOT_EXIST",
        "new_string": "x = 99",
    })
    assert "not found" in result.lower() or "error" in result.lower() or "failed" in result.lower()


def test_file_edit_batch_atomic(workspace):
    from agent.tools.file_ops import file_write, file_edit_batch, file_read

    # Write two files
    file_write.invoke({"file_path": "batch_a.py", "content": "A = 1\n"})
    file_write.invoke({"file_path": "batch_b.py", "content": "B = 2\n"})

    result = file_edit_batch.invoke({"edits": [
        {"file_path": "batch_a.py", "old_string": "A = 1", "new_string": "A = 100"},
        {"file_path": "batch_b.py", "old_string": "B = 2", "new_string": "B = 200"},
    ]})
    assert "error" not in result.lower() or "2" in result

    read_a = file_read.invoke({"file_path": "batch_a.py"})
    read_b = file_read.invoke({"file_path": "batch_b.py"})
    assert "A = 100" in read_a
    assert "B = 200" in read_b


def test_file_edit_batch_rollback_on_failure(workspace):
    from agent.tools.file_ops import file_write, file_edit_batch, file_read

    file_write.invoke({"file_path": "rollback_a.py", "content": "A = 1\n"})
    file_write.invoke({"file_path": "rollback_b.py", "content": "B = 2\n"})

    # Second edit will fail (string not found) → both should be rolled back
    result = file_edit_batch.invoke({"edits": [
        {"file_path": "rollback_a.py", "old_string": "A = 1", "new_string": "A = 999"},
        {"file_path": "rollback_b.py", "old_string": "DOES_NOT_EXIST", "new_string": "X"},
    ]})
    assert "error" in result.lower() or "failed" in result.lower() or "rollback" in result.lower()

    # rollback_a.py should still have original content
    read_a = file_read.invoke({"file_path": "rollback_a.py"})
    assert "A = 1" in read_a


def test_file_list(workspace):
    from agent.tools.file_ops import file_write, file_list

    file_write.invoke({"file_path": "listed.txt", "content": "x"})
    result = file_list.invoke({"path": "."})
    assert "listed.txt" in result


def test_glob_search(workspace):
    from agent.tools.file_ops import file_write, glob_search

    file_write.invoke({"file_path": "glob_a.py", "content": "pass"})
    file_write.invoke({"file_path": "glob_b.py", "content": "pass"})

    result = glob_search.invoke({"pattern": "*.py"})
    assert "glob_a.py" in result or "glob_b" in result


def test_file_read_nonexistent(workspace):
    from agent.tools.file_ops import file_read

    result = file_read.invoke({"file_path": "definitely_does_not_exist.py"})
    assert "not found" in result.lower() or "error" in result.lower()
