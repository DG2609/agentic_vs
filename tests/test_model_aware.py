"""Tests for agent/model_aware.py — model-specific tool adaptation."""
from unittest.mock import patch


class TestGetModelFamily:
    def test_openai_is_gpt(self):
        from agent.model_aware import get_model_family
        assert get_model_family("openai", "gpt-4o") == "gpt"

    def test_azure_is_gpt(self):
        from agent.model_aware import get_model_family
        assert get_model_family("azure_openai", "gpt-4") == "gpt"

    def test_anthropic_is_claude(self):
        from agent.model_aware import get_model_family
        assert get_model_family("anthropic", "claude-3-opus") == "claude"

    def test_google_is_gemini(self):
        from agent.model_aware import get_model_family
        assert get_model_family("google", "gemini-pro") == "gemini"

    def test_gemini_provider(self):
        from agent.model_aware import get_model_family
        assert get_model_family("gemini", "gemini-1.5-pro") == "gemini"

    def test_ollama_llama_is_gpt(self):
        from agent.model_aware import get_model_family
        assert get_model_family("ollama", "codellama:13b") == "gpt"

    def test_ollama_unknown_is_other(self):
        from agent.model_aware import get_model_family
        assert get_model_family("ollama", "mistral:7b") == "other"

    def test_groq_llama_is_gpt(self):
        from agent.model_aware import get_model_family
        assert get_model_family("groq", "llama-3.1-70b") == "gpt"

    def test_unknown_provider(self):
        from agent.model_aware import get_model_family
        assert get_model_family("unknown", "model") == "other"

    def test_default_from_config(self):
        from agent.model_aware import get_model_family
        with patch("agent.model_aware.config") as mock_config:
            mock_config.LLM_PROVIDER = "anthropic"
            assert get_model_family() == "claude"


class TestGetEditInstruction:
    def test_gpt_has_diff_instruction(self):
        from agent.model_aware import get_edit_instruction
        instr = get_edit_instruction("gpt")
        assert "unified diff" in instr.lower()

    def test_claude_has_structured_instruction(self):
        from agent.model_aware import get_edit_instruction
        instr = get_edit_instruction("claude")
        assert "old_string" in instr

    def test_gemini_has_instruction(self):
        from agent.model_aware import get_edit_instruction
        instr = get_edit_instruction("gemini")
        assert "file_edit" in instr

    def test_other_is_empty(self):
        from agent.model_aware import get_edit_instruction
        assert get_edit_instruction("other") == ""


class TestModelCapabilities:
    def test_gpt_has_vision(self):
        from agent.model_aware import get_model_capabilities
        caps = get_model_capabilities("gpt")
        assert caps["vision"] is True
        assert caps["json_mode"] is True

    def test_claude_no_json_mode(self):
        from agent.model_aware import get_model_capabilities
        caps = get_model_capabilities("claude")
        assert caps["vision"] is True
        assert caps["json_mode"] is False

    def test_other_no_vision(self):
        from agent.model_aware import get_model_capabilities
        caps = get_model_capabilities("other")
        assert caps["vision"] is False
