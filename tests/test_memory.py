"""
Tests for agent/tools/memory.py — SQLite FTS5 persistent memory
"""
import os
import pytest
import tempfile


@pytest.fixture(autouse=True)
def fresh_memory_db(tmp_path, monkeypatch):
    """Use a fresh in-memory DB per test."""
    import agent.tools.memory as mem_module
    import config

    db_path = str(tmp_path / "test_memory.db")
    monkeypatch.setattr(mem_module, "_DB_PATH", db_path)

    # Re-run _ensure_table to create fresh schema
    mem_module._ensure_table()
    yield
    # Cleanup handled by tmp_path fixture


def test_memory_save_and_search():
    from agent.tools.memory import memory_save, memory_search

    result = memory_save.invoke({
        "key": "auth_pattern",
        "value": "JWT tokens stored in Redis with 15min expiry",
        "tags": ["auth", "architecture"],
    })
    assert "saved" in result.lower()

    search = memory_search.invoke({"query": "JWT tokens", "n_results": 5})
    assert "auth_pattern" in search
    assert "JWT" in search


def test_memory_update_existing():
    from agent.tools.memory import memory_save, memory_search

    memory_save.invoke({"key": "db_host", "value": "localhost:5432"})
    result = memory_save.invoke({"key": "db_host", "value": "prod.db.example.com:5432"})
    assert "saved" in result.lower()  # atomic upsert — no separate "Updated" message

    search = memory_search.invoke({"query": "db_host"})
    assert "prod.db.example.com" in search
    assert "localhost" not in search


def test_memory_list():
    from agent.tools.memory import memory_save, memory_list

    memory_save.invoke({"key": "key1", "value": "value1", "tags": ["tag_a"]})
    memory_save.invoke({"key": "key2", "value": "value2", "tags": ["tag_b"]})

    result = memory_list.invoke({"tag": ""})
    assert "key1" in result
    assert "key2" in result


def test_memory_list_with_tag_filter():
    from agent.tools.memory import memory_save, memory_list

    memory_save.invoke({"key": "alpha", "value": "alpha value", "tags": ["group_a"]})
    memory_save.invoke({"key": "beta", "value": "beta value", "tags": ["group_b"]})

    result = memory_list.invoke({"tag": "group_a"})
    assert "alpha" in result
    assert "beta" not in result


def test_memory_delete():
    from agent.tools.memory import memory_save, memory_delete, memory_search

    memory_save.invoke({"key": "to_delete", "value": "will be gone"})
    delete_result = memory_delete.invoke({"key": "to_delete"})
    assert "Deleted" in delete_result

    search = memory_search.invoke({"query": "to_delete"})
    # The value should be gone; the no-results message may echo the query key
    assert "will be gone" not in search


def test_memory_delete_nonexistent():
    from agent.tools.memory import memory_delete

    result = memory_delete.invoke({"key": "ghost_key_xyz"})
    assert "not found" in result.lower() or "No memory" in result


def test_memory_search_no_results():
    from agent.tools.memory import memory_search

    result = memory_search.invoke({"query": "zzz_nonexistent_term_zzz"})
    assert "No memory" in result or "not found" in result.lower()


def test_memory_search_special_chars():
    """FTS5 query sanitization should not crash on special chars."""
    from agent.tools.memory import memory_save, memory_search

    memory_save.invoke({"key": "special", "value": "hello world"})
    # These would normally break FTS5
    for query in ["hello AND world", "hello OR (world)", '"quoted"', "test* wildcard"]:
        result = memory_search.invoke({"query": query})
        # Should not raise; may or may not find results
        assert isinstance(result, str)


def test_memory_fts5_sanitization():
    """Verify _sanitize_fts_query handles edge cases."""
    from agent.tools.memory import _sanitize_fts_query

    assert _sanitize_fts_query("hello world") == '"hello" "world"'
    assert _sanitize_fts_query("(broken*) AND test") == '"broken" "AND" "test"'
    assert _sanitize_fts_query("") == '""'
    assert _sanitize_fts_query('say "hi"') == '"say" "hi"'
