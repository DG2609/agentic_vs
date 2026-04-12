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


# ─────────────────────────────────────────────────────────────
# Line-ending awareness in file_edit
# ─────────────────────────────────────────────────────────────

def test_file_edit_preserves_crlf(workspace):
    """file_edit must preserve CRLF line endings when the original file uses them."""
    import os
    from agent.tools.file_ops import file_edit

    path = os.path.join(workspace, "crlf_test.txt")
    # Write a CRLF file directly (bypass file_write which uses default encoding)
    with open(path, "wb") as f:
        f.write(b"line1\r\nline2\r\nline3\r\n")

    result = file_edit.invoke({
        "file_path": path,
        "old_string": "line2",
        "new_string": "LINE_TWO",
    })
    assert "error" not in result.lower(), f"file_edit failed: {result}"

    with open(path, "rb") as f:
        raw = f.read()

    assert b"LINE_TWO" in raw, "replacement not found in file"
    assert b"\r\n" in raw, "CRLF line endings were not preserved"
    # There should be no bare LF (\n not preceded by \r)
    import re
    assert not re.search(rb"(?<!\r)\n", raw), "bare LF found — CRLF not preserved"


def test_file_edit_preserves_lf(workspace):
    """file_edit must preserve LF-only line endings."""
    import os
    from agent.tools.file_ops import file_edit

    path = os.path.join(workspace, "lf_test.txt")
    with open(path, "wb") as f:
        f.write(b"alpha\nbeta\ngamma\n")

    result = file_edit.invoke({
        "file_path": path,
        "old_string": "beta",
        "new_string": "BETA",
    })
    assert "error" not in result.lower(), f"file_edit failed: {result}"

    with open(path, "rb") as f:
        raw = f.read()

    assert b"BETA" in raw
    assert b"\r\n" not in raw, "CRLF appeared in LF-only file"


# ─────────────────────────────────────────────────────────────
# apply_patch tests
# ─────────────────────────────────────────────────────────────

def test_apply_patch_modify_file(workspace):
    """apply_patch can apply a simple unified diff to modify a file."""
    import os
    from agent.tools.file_ops import file_write, apply_patch

    path = "patch_target.py"
    abs_path = os.path.join(workspace, path)
    file_write.invoke({"file_path": path, "content": "x = 1\ny = 2\nz = 3\n"})

    # Build a minimal unified diff
    patch_text = (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n"
        " x = 1\n"
        "-y = 2\n"
        "+y = 20\n"
        " z = 3\n"
    )
    result = apply_patch.invoke({"patch": patch_text, "workspace": workspace})
    assert "error" not in result.lower(), f"apply_patch failed: {result}"
    assert "patched" in result.lower() or "applied" in result.lower()

    with open(abs_path, "r") as f:
        content = f.read()
    assert "y = 20" in content
    assert "y = 2\n" not in content


def test_apply_patch_create_file(workspace):
    """apply_patch can create a new file (--- /dev/null)."""
    import os
    from agent.tools.file_ops import apply_patch

    new_file = "created_by_patch.txt"
    abs_path = os.path.join(workspace, new_file)

    patch_text = (
        f"--- /dev/null\n"
        f"+++ b/{new_file}\n"
        "@@ -0,0 +1,2 @@\n"
        "+hello\n"
        "+world\n"
    )
    result = apply_patch.invoke({"patch": patch_text, "workspace": workspace})
    assert "error" not in result.lower(), f"apply_patch creation failed: {result}"
    assert os.path.isfile(abs_path), "new file was not created"

    with open(abs_path, "r") as f:
        content = f.read()
    assert "hello" in content
    assert "world" in content


def test_apply_patch_delete_file(workspace):
    """apply_patch can delete a file (+++ /dev/null)."""
    import os
    from agent.tools.file_ops import file_write, apply_patch

    path = "to_delete.txt"
    abs_path = os.path.join(workspace, path)
    file_write.invoke({"file_path": path, "content": "temporary\n"})
    assert os.path.isfile(abs_path)

    patch_text = (
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-temporary\n"
    )
    result = apply_patch.invoke({"patch": patch_text, "workspace": workspace})
    assert "error" not in result.lower(), f"apply_patch deletion failed: {result}"
    assert not os.path.isfile(abs_path), "file should have been deleted"


def test_apply_patch_invalid_path(workspace):
    """apply_patch rejects paths outside the workspace."""
    from agent.tools.file_ops import apply_patch

    patch_text = (
        "--- a/../../etc/passwd\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1 +1 @@\n"
        "-root\n"
        "+hacked\n"
    )
    result = apply_patch.invoke({"patch": patch_text, "workspace": workspace})
    assert "error" in result.lower() or "outside" in result.lower() or "boundary" in result.lower()
