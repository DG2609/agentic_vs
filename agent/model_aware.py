"""
Model-aware tool adaptation — adjust tool behavior based on the active LLM provider.

Key adaptations:
- GPT models: Use unified diff / apply_patch format for file edits
- Claude models: Use structured old_string/new_string edit format
- Gemini models: Similar to Claude
- Ollama: Depends on underlying model

This module provides:
- get_model_family(provider, model) → "gpt" | "claude" | "gemini" | "other"
- get_edit_instruction(family) → instructions for the system prompt
- adapt_tool_descriptions(tools, family) → modify tool descriptions for model
"""

import logging

import config

logger = logging.getLogger(__name__)


def get_model_family(provider: str = "", model: str = "") -> str:
    """Determine the model family from provider/model config.

    Returns one of: "gpt", "claude", "gemini", "other"
    """
    provider = provider or getattr(config, "LLM_PROVIDER", "openai")
    provider = provider.lower()

    if provider in ("openai", "azure_openai"):
        return "gpt"
    if provider == "anthropic":
        return "claude"
    if provider in ("google", "gemini"):
        return "gemini"
    if provider == "ollama":
        model = model or getattr(config, "OLLAMA_MODEL", "")
        model_lower = model.lower()
        if "llama" in model_lower or "codellama" in model_lower:
            return "gpt"  # Llama works better with diff format
        if "claude" in model_lower or "anthropic" in model_lower:
            return "claude"
        return "other"
    if provider == "groq":
        model = model or getattr(config, "GROQ_MODEL", "")
        if "llama" in model.lower():
            return "gpt"
        return "other"

    return "other"


# Model-specific file edit instructions appended to system prompt
_EDIT_INSTRUCTIONS = {
    "gpt": """
## File Edit Format
When using file_edit, provide changes as a unified diff when possible:
- Include enough context lines (3+) around changes for unambiguous matching
- For the old_string parameter, include the exact text to replace
- For large changes, prefer file_edit_batch to group multiple edits
- You may use file_write for complete file rewrites
""",
    "claude": """
## File Edit Format
When using file_edit:
- Provide the exact old_string to match (copy-paste precision)
- Provide the new_string replacement
- Include enough surrounding context to make old_string unique in the file
- For multiple changes to one file, use file_edit_batch
""",
    "gemini": """
## File Edit Format
When using file_edit:
- Provide the exact old_string and new_string
- Include surrounding context lines to ensure uniqueness
- Use file_edit_batch for multiple edits to the same file
""",
    "other": "",
}


def get_edit_instruction(family: str = "") -> str:
    """Get model-specific edit format instructions for the system prompt."""
    if not family:
        family = get_model_family()
    return _EDIT_INSTRUCTIONS.get(family, "")


# Model capabilities — which features each family supports
_MODEL_CAPABILITIES = {
    "gpt": {"vision": True, "tool_use": True, "json_mode": True, "streaming": True},
    "claude": {"vision": True, "tool_use": True, "json_mode": False, "streaming": True},
    "gemini": {"vision": True, "tool_use": True, "json_mode": True, "streaming": True},
    "other": {"vision": False, "tool_use": True, "json_mode": False, "streaming": True},
}


def get_model_capabilities(family: str = "") -> dict:
    """Get capability flags for the model family."""
    if not family:
        family = get_model_family()
    return _MODEL_CAPABILITIES.get(family, _MODEL_CAPABILITIES["other"])
