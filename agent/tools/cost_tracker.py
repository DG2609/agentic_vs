"""
Cost tracking for LLM API calls.

Tracks per-session token usage and calculates USD cost based on model pricing.
Provides calculate_cost(), format_cost(), and session accumulation helpers.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Per-model pricing (USD per 1M tokens) as of 2026-04
MODEL_COSTS: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6":     {"input": 30.0,  "output": 150.0, "cache_read": 3.0,   "cache_write": 7.5},
    "claude-sonnet-4-6":   {"input": 3.0,   "output": 15.0,  "cache_read": 0.3,   "cache_write": 0.75},
    "claude-haiku-4-5":    {"input": 0.8,   "output": 4.0,   "cache_read": 0.08,  "cache_write": 0.2},
    # Aliases for backward compat
    "claude-3-opus":       {"input": 30.0,  "output": 150.0, "cache_read": 3.0,   "cache_write": 7.5},
    "claude-3-sonnet":     {"input": 3.0,   "output": 15.0,  "cache_read": 0.3,   "cache_write": 0.75},
    "claude-3-haiku":      {"input": 0.8,   "output": 4.0,   "cache_read": 0.08,  "cache_write": 0.2},
    # OpenAI
    "gpt-4o":              {"input": 5.0,   "output": 15.0,  "cache_read": 2.5,   "cache_write": 0.0},
    "gpt-4o-mini":         {"input": 0.15,  "output": 0.6,   "cache_read": 0.075, "cache_write": 0.0},
    "gpt-4-turbo":         {"input": 10.0,  "output": 30.0,  "cache_read": 0.0,   "cache_write": 0.0},
    # Google Gemini
    "gemini-1.5-pro":      {"input": 3.5,   "output": 10.5,  "cache_read": 0.875, "cache_write": 0.0},
    "gemini-1.5-flash":    {"input": 0.075, "output": 0.3,   "cache_read": 0.01875, "cache_write": 0.0},
    # Groq (free tier / varies)
    "llama3-70b-8192":     {"input": 0.59,  "output": 0.79,  "cache_read": 0.0,   "cache_write": 0.0},
    "mixtral-8x7b-32768":  {"input": 0.24,  "output": 0.24,  "cache_read": 0.0,   "cache_write": 0.0},
    # Ollama (self-hosted, no cost)
    "ollama":              {"input": 0.0,   "output": 0.0,   "cache_read": 0.0,   "cache_write": 0.0},
}


def _normalize_model(model: str) -> str:
    """Normalize model name for lookup — case-insensitive prefix matching."""
    if not model:
        return ""
    model_lower = model.lower()
    # Exact match
    if model_lower in MODEL_COSTS:
        return model_lower
    # Prefix match (e.g. "claude-sonnet-4-6-20250101" → "claude-sonnet-4-6")
    for key in MODEL_COSTS:
        if model_lower.startswith(key):
            return key
    # Pattern match: "claude-opus" → "claude-opus-4-6", "gpt-4o" → exact, etc.
    if "ollama" in model_lower:
        return "ollama"
    if "gemini-1.5-pro" in model_lower:
        return "gemini-1.5-pro"
    if "gemini-1.5-flash" in model_lower:
        return "gemini-1.5-flash"
    if "gpt-4o-mini" in model_lower:
        return "gpt-4o-mini"
    if "gpt-4o" in model_lower:
        return "gpt-4o"
    if "gpt-4" in model_lower:
        return "gpt-4-turbo"
    if "claude" in model_lower and "opus" in model_lower:
        return "claude-opus-4-6"
    if "claude" in model_lower and "haiku" in model_lower:
        return "claude-haiku-4-5"
    if "claude" in model_lower and ("sonnet" in model_lower or "claude-3" in model_lower):
        return "claude-sonnet-4-6"
    if "llama" in model_lower:
        return "llama3-70b-8192"
    if "mixtral" in model_lower:
        return "mixtral-8x7b-32768"
    return ""


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Calculate USD cost for a single LLM call.

    Args:
        model:              Model identifier (e.g. 'claude-sonnet-4-6', 'gpt-4o').
        input_tokens:       Number of input/prompt tokens.
        output_tokens:      Number of output/completion tokens.
        cache_read_tokens:  Prompt cache read tokens (Anthropic only).
        cache_write_tokens: Prompt cache write tokens (Anthropic only).

    Returns:
        Estimated cost in USD (float). Returns 0.0 if model is unknown.
    """
    key = _normalize_model(model)
    if not key or key not in MODEL_COSTS:
        logger.debug("[cost_tracker] Unknown model '%s' — cost = $0.00", model)
        return 0.0

    prices = MODEL_COSTS[key]
    per_m = 1_000_000

    cost = (
        (input_tokens       / per_m) * prices["input"]
        + (output_tokens    / per_m) * prices["output"]
        + (cache_read_tokens  / per_m) * prices.get("cache_read", 0.0)
        + (cache_write_tokens / per_m) * prices.get("cache_write", 0.0)
    )
    return cost


def format_cost(usd: float) -> str:
    """Format a USD cost for human-readable display.

    Examples:
        0.0         → '$0.00'
        0.0001      → '<$0.01'
        0.0234      → '$0.023'
        1.2345      → '$1.23'
        12.50       → '$12.50'
    """
    if usd == 0.0:
        return "$0.00"
    if usd < 0.01:
        return "<$0.01"
    if usd < 1.0:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def extract_tokens_from_response(response) -> tuple[int, int, int, int]:
    """Extract token counts from a LangChain AIMessage response.

    Returns:
        (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    """
    um = getattr(response, "usage_metadata", None)
    if not um:
        return 0, 0, 0, 0

    if isinstance(um, dict):
        return (
            (um.get("input_tokens", 0) or 0),
            (um.get("output_tokens", 0) or 0),
            (um.get("cache_read_input_tokens", 0) or 0),
            (um.get("cache_creation_input_tokens", 0) or 0),
        )

    # Object-style usage_metadata (some providers)
    return (
        getattr(um, "input_tokens", 0) or 0,
        getattr(um, "output_tokens", 0) or 0,
        getattr(um, "cache_read_input_tokens", 0) or 0,
        getattr(um, "cache_creation_input_tokens", 0) or 0,
    )


def cost_from_response(model: str, response) -> float:
    """Calculate cost from a LangChain AIMessage response object.

    Convenience wrapper around calculate_cost + extract_tokens_from_response.
    """
    inp, out, cache_read, cache_write = extract_tokens_from_response(response)
    return calculate_cost(model, inp, out, cache_read, cache_write)
