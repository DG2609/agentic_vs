"""
Configuration for the Agentic System.
Uses Pydantic BaseSettings for type-safe, validated configuration.
All settings can be overridden via environment variables or .env file.
"""
import os
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


# ── Resolve base paths before Settings class ────────────────
_BASE_DIR = Path(__file__).parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    """
    Validated configuration. All fields read from env vars automatically.
    E.g. LLM_PROVIDER env var → Settings.LLM_PROVIDER field.
    """
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── LLM Provider ────────────────────────────────────────
    LLM_PROVIDER: Literal["ollama", "openai", "anthropic", "google", "github"] = Field(
        default="ollama",
        description=(
            "LLM backend: 'ollama' (self-hosted), 'openai', 'anthropic', "
            "'google' (Gemini), or 'github' (GitHub Models — OpenAI-compatible)."
        )
    )

    # Vector backend for semantic search
    VECTOR_BACKEND: str = Field(
        default="chroma",
        description="Vector DB backend: 'chroma' (default, no Docker) or 'milvus' (Docker required)."
    )

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:14b"
    OLLAMA_FAST_MODEL: str = ""  # empty = fall back to OLLAMA_MODEL
    EMBEDDING_MODEL: str = "nomic-embed-text"

    # OpenAI (also used by 'github' provider via OPENAI_BASE_URL override)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_FAST_MODEL: str = "gpt-4o-mini"  # cheaper/faster for subagents & summarization
    OPENAI_BASE_URL: str = ""               # override for GitHub Models: https://models.inference.ai.azure.com

    # Anthropic (Claude)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_FAST_MODEL: str = "claude-haiku-4-5-20251001"

    # Google (Gemini)
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-2.0-flash"
    GOOGLE_FAST_MODEL: str = "gemini-2.0-flash-lite"

    # GitHub Models (uses OpenAI-compatible API — set OPENAI_BASE_URL + OPENAI_API_KEY)
    # LLM_PROVIDER=github → same as openai but with OPENAI_BASE_URL=https://models.inference.ai.azure.com
    # OPENAI_API_KEY = your GitHub PAT (ghp_xxxx)

    # ── Server ──────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8000, ge=1, le=65535)
    # Optional API key for server auth (empty = no auth required)
    API_KEY: str = Field(default="", description="If set, clients must send this key in the 'x-api-key' header or 'api_key' field.")
    # Hard timeout for a single agent run in seconds (0 = no timeout)
    AGENT_TIMEOUT: int = Field(default=0, ge=0, description="Max seconds per agent run. 0 = unlimited.")

    # ── Tools ───────────────────────────────────────────────
    TOOL_TIMEOUT: int = Field(default=30, ge=5, le=300, description="Seconds per tool execution")
    MAX_TERMINAL_OUTPUT: int = Field(default=10000, ge=1000)
    WORKSPACE_DIR: str = Field(default_factory=lambda: str(_BASE_DIR / "workspace"))

    # Truncation
    MAX_OUTPUT_LINES: int = Field(default=2000, ge=100, description="Max lines per tool output")
    MAX_OUTPUT_BYTES: int = Field(default=50 * 1024, ge=1024, description="Max bytes per tool output")

    # Ripgrep
    RIPGREP_PATH: str = Field(default="rg", description="Path to ripgrep binary")

    # ── Memory / Compaction ─────────────────────────────────
    MAX_MESSAGES_BEFORE_SUMMARY: int = Field(
        default=20, ge=6,
        description="Fallback: summarize after N messages"
    )
    COMPACTION_BUFFER: int = Field(
        default=20000, ge=1000,
        description="Trigger compaction when tokens exceed model_limit - buffer"
    )
    PRUNE_MINIMUM: int = Field(default=20000, ge=1000)
    PRUNE_PROTECT: int = Field(default=40000, ge=1000)

    # ── Model context limits ────────────────────────────────
    MODEL_CONTEXT_LIMITS: dict[str, int] = Field(default_factory=lambda: {
        # Ollama
        "qwen2.5:14b": 32768,
        "qwen2.5:32b": 32768,
        "qwen2.5:7b": 32768,
        "qwen2.5-coder:7b": 32768,
        "qwen2.5-coder:14b": 32768,
        "qwen2.5-coder:32b": 32768,
        "llama3.1:8b": 128000,
        "devstral": 128000,
        # OpenAI
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16384,
        # Anthropic
        "claude-opus-4-6": 200000,
        "claude-sonnet-4-6": 200000,
        "claude-haiku-4-5-20251001": 200000,
        "claude-3-5-sonnet-20241022": 200000,
        "claude-3-5-haiku-20241022": 200000,
        # Google Gemini
        "gemini-2.0-flash": 1048576,
        "gemini-2.0-flash-lite": 1048576,
        "gemini-1.5-pro": 2097152,
        "gemini-1.5-flash": 1048576,
        # GitHub Models (subset available via inference.ai.azure.com)
        "Meta-Llama-3.1-70B-Instruct": 128000,
        "Meta-Llama-3.1-405B-Instruct": 128000,
        "Mistral-large": 128000,
        "Phi-3.5-MoE-instruct": 131072,
    })

    # ── Project rules ───────────────────────────────────────
    RULES_FILENAMES: list[str] = Field(
        default_factory=lambda: ["AGENTS.md", "CLAUDE.md", "COPILOT.md", ".cursorrules"]
    )

    @field_validator("WORKSPACE_DIR")
    @classmethod
    def ensure_workspace_exists(cls, v):
        os.makedirs(v, exist_ok=True)
        return v

    @field_validator("PRUNE_PROTECT")
    @classmethod
    def protect_gte_minimum(cls, v, info):
        minimum = info.data.get("PRUNE_MINIMUM", 20000)
        if v < minimum:
            raise ValueError(f"PRUNE_PROTECT ({v}) must be >= PRUNE_MINIMUM ({minimum})")
        return v

    @field_validator(
        "OLLAMA_MODEL", "OPENAI_MODEL", "OLLAMA_FAST_MODEL", "OPENAI_FAST_MODEL",
        "ANTHROPIC_MODEL", "ANTHROPIC_FAST_MODEL", "GOOGLE_MODEL", "GOOGLE_FAST_MODEL",
    )
    @classmethod
    def warn_unknown_model(cls, v, info):
        """Emit a warning (not error) for models not in MODEL_CONTEXT_LIMITS."""
        import warnings
        if not v:
            return v  # empty = use fallback
        known = info.data.get("MODEL_CONTEXT_LIMITS", {})
        if known and v not in known:
            warnings.warn(
                f"Model '{v}' is not in MODEL_CONTEXT_LIMITS — token-based compaction "
                f"will use a default limit. Add it to MODEL_CONTEXT_LIMITS in .env if needed.",
                stacklevel=2,
            )
        return v


# ── Instantiate once (singleton) ────────────────────────────
_settings = Settings()

# ── Export as module-level attrs for backward compatibility ──
# Every `import config; config.WORKSPACE_DIR` still works.
BASE_DIR = _BASE_DIR
DATA_DIR = _DATA_DIR
STATIC_DIR = _BASE_DIR / "static"

VECTOR_BACKEND = _settings.VECTOR_BACKEND
LLM_PROVIDER = _settings.LLM_PROVIDER
OLLAMA_BASE_URL = _settings.OLLAMA_BASE_URL
OLLAMA_MODEL = _settings.OLLAMA_MODEL
OLLAMA_FAST_MODEL = _settings.OLLAMA_FAST_MODEL
EMBEDDING_MODEL = _settings.EMBEDDING_MODEL
OPENAI_API_KEY = _settings.OPENAI_API_KEY
OPENAI_MODEL = _settings.OPENAI_MODEL
OPENAI_FAST_MODEL = _settings.OPENAI_FAST_MODEL
OPENAI_BASE_URL = _settings.OPENAI_BASE_URL

ANTHROPIC_API_KEY = _settings.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = _settings.ANTHROPIC_MODEL
ANTHROPIC_FAST_MODEL = _settings.ANTHROPIC_FAST_MODEL

GOOGLE_API_KEY = _settings.GOOGLE_API_KEY
GOOGLE_MODEL = _settings.GOOGLE_MODEL
GOOGLE_FAST_MODEL = _settings.GOOGLE_FAST_MODEL

HOST = _settings.HOST
PORT = _settings.PORT
API_KEY = _settings.API_KEY
AGENT_TIMEOUT = _settings.AGENT_TIMEOUT

TOOL_TIMEOUT = _settings.TOOL_TIMEOUT
MAX_TERMINAL_OUTPUT = _settings.MAX_TERMINAL_OUTPUT
WORKSPACE_DIR = _settings.WORKSPACE_DIR
MAX_OUTPUT_LINES = _settings.MAX_OUTPUT_LINES
MAX_OUTPUT_BYTES = _settings.MAX_OUTPUT_BYTES
RIPGREP_PATH = _settings.RIPGREP_PATH

MAX_MESSAGES_BEFORE_SUMMARY = _settings.MAX_MESSAGES_BEFORE_SUMMARY
COMPACTION_BUFFER = _settings.COMPACTION_BUFFER
PRUNE_MINIMUM = _settings.PRUNE_MINIMUM
PRUNE_PROTECT = _settings.PRUNE_PROTECT
MODEL_CONTEXT_LIMITS = _settings.MODEL_CONTEXT_LIMITS
RULES_FILENAMES = _settings.RULES_FILENAMES
