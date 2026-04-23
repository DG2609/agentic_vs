"""Tests for per-tool JSON schema plumbing from plugin host → _ProxyTool."""
from pathlib import Path
import pytest

from agent.plugins.manager import _build_args_model, _ProxyArgs
from agent.plugins.sandbox import RuntimeSandbox

FIX = Path(__file__).parent / "fixtures"


def test_build_args_model_from_schema_extracts_fields():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "who to greet"},
            "excited": {"type": "boolean", "default": False},
        },
        "required": ["name"],
    }
    model = _build_args_model("say_hi", schema)
    assert model is not _ProxyArgs
    js = model.model_json_schema()
    assert "name" in js["properties"]
    assert "excited" in js["properties"]
    assert "name" in js.get("required", [])


def test_build_args_model_empty_schema_falls_back_to_permissive():
    assert _build_args_model("x", {}) is _ProxyArgs
    assert _build_args_model("x", {"type": "object", "properties": {}}) is _ProxyArgs


@pytest.mark.asyncio
async def test_handshake_returns_tool_schema():
    """Plugin host should return the full args_schema for each tool."""
    sb = RuntimeSandbox(plugin_dir=FIX / "good_plugin", permissions=[])
    await sb.start()
    try:
        descs = sb.tool_descriptors()
        assert descs, "handshake returned no tool descriptors"
        by_name = {d["name"]: d for d in descs}
        assert "say_hi" in by_name
        schema = by_name["say_hi"].get("schema")
        assert isinstance(schema, dict)
        # say_hi(name: str) — LangChain generates an args_schema with a `name` field.
        assert "name" in (schema.get("properties") or {})
    finally:
        await sb.stop()
