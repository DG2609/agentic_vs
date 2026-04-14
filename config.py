"""
Configuration for the Agentic System.
Uses Pydantic BaseSettings for type-safe, validated configuration.
All settings can be overridden via environment variables or .env file.
"""
import os
import re as _re
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


def _expand_env_refs(value: str) -> str:
    """Expand {env:VAR} references in a config string. Warns for unset vars."""
    def _sub(m: _re.Match) -> str:
        var = m.group(1)
        result = os.environ.get(var, "")
        if not result:
            import logging
            logging.getLogger(__name__).warning(
                "Config: {env:%s} references unset env var", var
            )
        return result
    return _re.sub(r'\{env:([^}]+)\}', _sub, value)


# ── Resolve base paths before Settings class ────────────────
_BASE_DIR = Path(__file__).parent
_DATA_DIR = _BASE_DIR / "data"
try:
    _DATA_DIR.mkdir(exist_ok=True)
except OSError as e:
    import tempfile as _tempfile
    _DATA_DIR = Path(_tempfile.gettempdir()) / "shadowdev_data"
    _DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    """
    Validated configuration. All fields read from env vars automatically.
    E.g. LLM_PROVIDER env var → Settings.LLM_PROVIDER field.
    """
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── LLM Provider ────────────────────────────────────────
    LLM_PROVIDER: Literal[
        "ollama", "openai", "anthropic", "google", "groq", "azure",
        "vllm", "llamacpp", "lmstudio", "openai_compatible",
        "vertex_ai", "github_copilot", "aws_bedrock", "mistral",
        "together", "fireworks", "deepseek", "perplexity", "xai",
    ] = Field(
        default="ollama",
        description=(
            "LLM backend. Local: 'ollama', 'vllm', 'llamacpp', 'lmstudio', 'openai_compatible'. "
            "Cloud: 'openai', 'anthropic', 'google', 'groq', 'azure', 'vertex_ai', "
            "'github_copilot', 'aws_bedrock', 'mistral', 'together', 'fireworks', "
            "'deepseek', 'perplexity', 'xai'."
        ),
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

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_FAST_MODEL: str = "gpt-4o-mini"  # cheaper/faster for subagents & summarization

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_FAST_MODEL: str = ""

    # Google Gemini
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-2.0-flash"
    GOOGLE_FAST_MODEL: str = ""

    # Groq
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_FAST_MODEL: str = ""

    # Azure OpenAI
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_MODEL: str = "gpt-4o"
    AZURE_OPENAI_FAST_MODEL: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"

    # vLLM (OpenAI-compatible server)
    VLLM_BASE_URL: str = Field(default="http://localhost:8000/v1", description="vLLM server URL (OpenAI-compatible)")
    VLLM_MODEL: str = Field(default="", description="Model name as served by vLLM (e.g. 'meta-llama/Llama-3.1-8B-Instruct')")
    VLLM_FAST_MODEL: str = Field(default="", description="Fast/cheap vLLM model for subagents")
    VLLM_API_KEY: str = Field(default="EMPTY", description="vLLM API key (usually 'EMPTY' for local deployments)")

    # llama.cpp server (OpenAI-compatible)
    LLAMACPP_BASE_URL: str = Field(default="http://localhost:8080/v1", description="llama.cpp server URL")
    LLAMACPP_MODEL: str = Field(default="local-model", description="Model name for llama.cpp (usually 'local-model' or leave empty)")
    LLAMACPP_FAST_MODEL: str = Field(default="", description="Fast model for subagents")
    LLAMACPP_API_KEY: str = Field(default="EMPTY", description="llama.cpp API key (usually 'EMPTY')")

    # LM Studio (OpenAI-compatible)
    LMSTUDIO_BASE_URL: str = Field(default="http://localhost:1234/v1", description="LM Studio local server URL")
    LMSTUDIO_MODEL: str = Field(default="", description="Model ID loaded in LM Studio (leave empty to use first available)")
    LMSTUDIO_FAST_MODEL: str = Field(default="", description="Fast model for subagents")
    LMSTUDIO_API_KEY: str = Field(default="lm-studio", description="LM Studio API key (default 'lm-studio')")

    # Generic OpenAI-compatible endpoint
    OPENAI_COMPATIBLE_BASE_URL: str = Field(default="http://localhost:8000/v1", description="Any OpenAI-compatible API base URL")
    OPENAI_COMPATIBLE_MODEL: str = Field(default="", description="Model name for the OpenAI-compatible endpoint")
    OPENAI_COMPATIBLE_FAST_MODEL: str = Field(default="", description="Fast model")
    OPENAI_COMPATIBLE_API_KEY: str = Field(default="EMPTY", description="API key for the OpenAI-compatible endpoint")
    OPENAI_COMPATIBLE_NAME: str = Field(default="custom", description="Human-readable name for this provider (e.g. 'Together AI', 'DeepInfra')")

    # Google Cloud Vertex AI
    VERTEX_AI_PROJECT: str = Field(default="", description="GCP project ID for Vertex AI (uses ADC if empty)")
    VERTEX_AI_LOCATION: str = Field(default="us-central1", description="GCP region for Vertex AI")
    VERTEX_AI_MODEL: str = Field(default="gemini-2.0-flash-001", description="Vertex AI model name")
    VERTEX_AI_FAST_MODEL: str = Field(default="", description="Fast/cheap Vertex AI model for subagents")

    # GitHub Copilot (OpenAI-compatible)
    GITHUB_COPILOT_API_KEY: str = Field(default="", description="GitHub Copilot API token (OAuth Bearer or PAT)")
    GITHUB_COPILOT_MODEL: str = Field(default="gpt-4o", description="Model via GitHub Copilot API")
    GITHUB_COPILOT_FAST_MODEL: str = Field(default="gpt-4o-mini", description="Fast model for subagents")

    # AWS Bedrock
    AWS_REGION: str = Field(default="us-east-1", description="AWS region for Bedrock API calls")
    AWS_ACCESS_KEY_ID: str = Field(default="", description="AWS Access Key ID (uses IAM role/env if empty)")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", description="AWS Secret Access Key")
    BEDROCK_MODEL: str = Field(default="anthropic.claude-3-5-sonnet-20241022-v2:0", description="AWS Bedrock model ID")
    BEDROCK_FAST_MODEL: str = Field(default="", description="Fast Bedrock model for subagents")

    # Mistral AI
    MISTRAL_API_KEY: str = Field(default="", description="Mistral AI API key")
    MISTRAL_MODEL: str = Field(default="mistral-large-latest", description="Mistral AI model name")
    MISTRAL_FAST_MODEL: str = Field(default="mistral-small-latest", description="Fast/cheap Mistral model")

    # Together AI (OpenAI-compatible)
    TOGETHER_API_KEY: str = Field(default="", description="Together AI API key")
    TOGETHER_MODEL: str = Field(default="meta-llama/Llama-3.3-70B-Instruct-Turbo", description="Together AI model name")
    TOGETHER_FAST_MODEL: str = Field(default="", description="Fast Together AI model for subagents")

    # Fireworks AI (OpenAI-compatible)
    FIREWORKS_API_KEY: str = Field(default="", description="Fireworks AI API key")
    FIREWORKS_MODEL: str = Field(default="accounts/fireworks/models/llama-v3p3-70b-instruct", description="Fireworks AI model name")
    FIREWORKS_FAST_MODEL: str = Field(default="", description="Fast Fireworks AI model for subagents")

    # DeepSeek (OpenAI-compatible)
    DEEPSEEK_API_KEY: str = Field(default="", description="DeepSeek API key")
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat", description="DeepSeek model (deepseek-chat or deepseek-reasoner)")
    DEEPSEEK_FAST_MODEL: str = Field(default="", description="Fast DeepSeek model for subagents")

    # Perplexity AI (OpenAI-compatible)
    PERPLEXITY_API_KEY: str = Field(default="", description="Perplexity AI API key")
    PERPLEXITY_MODEL: str = Field(default="sonar-pro", description="Perplexity AI model name")
    PERPLEXITY_FAST_MODEL: str = Field(default="sonar", description="Fast/cheap Perplexity model")

    # xAI / Grok (OpenAI-compatible)
    XAI_API_KEY: str = Field(default="", description="xAI API key for Grok models")
    XAI_MODEL: str = Field(default="grok-3", description="xAI Grok model name")
    XAI_FAST_MODEL: str = Field(default="grok-3-mini", description="Fast xAI model for subagents")

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
        "qwen2.5:14b": 32768,
        "qwen2.5:32b": 32768,
        "qwen2.5:7b": 32768,
        "qwen2.5-coder:7b": 32768,
        "qwen2.5-coder:14b": 32768,
        "qwen2.5-coder:32b": 32768,
        "llama3.1:8b": 128000,
        "devstral": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-3.5-turbo": 16384,
        "claude-sonnet-4-20250514": 200000,
        "claude-haiku-4-5-20251001": 200000,
        "gemini-2.0-flash": 1048576,
        "gemini-2.5-pro": 1048576,
        "llama-3.3-70b-versatile": 131072,
        "meta-llama/Llama-3.1-8B-Instruct": 131072,
        "meta-llama/Llama-3.1-70B-Instruct": 131072,
        "meta-llama/Llama-3.3-70B-Instruct": 131072,
        "mistralai/Mistral-7B-Instruct-v0.3": 32768,
        "mistralai/Mixtral-8x7B-Instruct-v0.1": 32768,
        "microsoft/Phi-3.5-mini-instruct": 128000,
        "Qwen/Qwen2.5-7B-Instruct": 32768,
        "Qwen/Qwen2.5-72B-Instruct": 32768,
        "local-model": 32768,
        # Vertex AI
        "gemini-2.0-flash-001": 1048576,
        "gemini-1.5-pro": 2097152,
        "gemini-1.5-flash": 1048576,
        # AWS Bedrock
        "anthropic.claude-3-5-sonnet-20241022-v2:0": 200000,
        "anthropic.claude-3-5-haiku-20241022-v1:0": 200000,
        "anthropic.claude-3-haiku-20240307-v1:0": 200000,
        "amazon.titan-text-premier-v1:0": 32768,
        "meta.llama3-70b-instruct-v1:0": 128000,
        # Mistral AI
        "mistral-large-latest": 131072,
        "mistral-small-latest": 32768,
        "codestral-latest": 32768,
        # Together AI
        "meta-llama/Llama-3.3-70B-Instruct-Turbo": 131072,
        "meta-llama/Llama-3.1-405B-Instruct-Turbo": 130815,
        "Qwen/Qwen2.5-72B-Instruct-Turbo": 32768,
        # Fireworks AI
        "accounts/fireworks/models/llama-v3p3-70b-instruct": 131072,
        "accounts/fireworks/models/deepseek-r1": 163840,
        # DeepSeek
        "deepseek-chat": 64000,
        "deepseek-reasoner": 64000,
        # Perplexity
        "sonar-pro": 200000,
        "sonar": 127072,
        "sonar-reasoning-pro": 128000,
        # xAI / Grok
        "grok-3": 131072,
        "grok-3-mini": 131072,
        "grok-2": 131072,
    })

    # ── Container Sandbox ─────────────────────────────────────
    SANDBOX_ENABLED: bool = Field(
        default=False,
        description=(
            "Run terminal_exec inside a Docker container for OS-level isolation. "
            "Requires Docker daemon to be running. Falls back to direct execution if Docker "
            "is unavailable."
        ),
    )
    SANDBOX_IMAGE: str = Field(
        default="python:3.12-slim",
        description="Docker image used for the sandbox container.",
    )
    SANDBOX_NETWORK: str = Field(
        default="none",
        description="Docker network mode: 'none' (full isolation), 'bridge', or 'host'.",
    )
    SANDBOX_MEMORY: str = Field(
        default="512m",
        description="Memory limit for the sandbox container (Docker format, e.g. '512m', '1g').",
    )
    SANDBOX_CPUS: str = Field(
        default="1.0",
        description="CPU quota for the sandbox container (e.g. '1.0' = one CPU).",
    )
    SANDBOX_PIDS_LIMIT: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="Maximum number of processes inside the sandbox container.",
    )
    SANDBOX_READONLY: bool = Field(
        default=False,
        description=(
            "Mount workspace read-only inside the sandbox. "
            "Useful for analysis-only tasks. Default False (read-write)."
        ),
    )

    # ── Hooks ─────────────────────────────────────────────────
    HOOKS_FILE: str = Field(default="", description="Path to hooks config JSON. Empty = no hooks.")

    # ── GitHub / GitLab ───────────────────────────────────────
    GITHUB_TOKEN: str = ""
    GITLAB_TOKEN: str = ""
    GITLAB_INSTANCE_URL: str = "https://gitlab.com"

    # ── MCP Servers ───────────────────────────────────────────
    MCP_SERVERS: dict = Field(default_factory=dict, description="MCP server definitions (JSON dict)")

    # ── Project rules ───────────────────────────────────────
    RULES_FILENAMES: list[str] = Field(
        default_factory=lambda: ["AGENTS.md", "CLAUDE.md", "COPILOT.md", ".cursorrules"]
    )

    # ── UI / Theme ───────────────────────────────────────────
    THEME: str = Field(
        default="default",
        description="Color theme: 'default', 'dark', 'daltonized-light', or 'daltonized-dark'.",
    )

    # ── Reasoning effort ────────────────────────────────────
    REASONING_EFFORT: Literal["none", "low", "medium", "high"] = Field(
        default="none",
        description=(
            "Reasoning effort level for models that support extended thinking. "
            "'none' = disabled. Supported by Claude Opus 4+ (Anthropic) and "
            "OpenAI o1/o3 series models."
        ),
    )

    # ── Notifications ────────────────────────────────────────
    NOTIFY_ON_COMPLETE: bool = Field(
        default=True,
        description="Send a desktop notification when a long-running agent task completes (> 10 s).",
    )

    # ── Model Advisor ────────────────────────────────────────
    ADVISOR_MODEL: str = Field(
        default="",
        description="Optional advisor model name (e.g. 'claude-opus-4-6'). When set, runs a second model in parallel to critique/suggest improvements. Empty = disabled."
    )

    # ── Undercover mode ──────────────────────────────────────
    UNDERCOVER_MODE: bool = Field(
        default=False,
        description="Strip internal AI model codenames from commits/PRs (for open-source contributions).",
    )

    # ── Auto Dream ───────────────────────────────────────────
    AUTO_DREAM_ENABLED: bool = Field(
        default=True,
        description="Enable background memory consolidation every AUTO_DREAM_INTERVAL turns.",
    )

    # ── Agent Teams ──────────────────────────────────────────
    COORDINATOR_MODE: bool = Field(
        default=False,
        description="Activate coordinator multi-agent mode (set by --team flag).",
    )
    TEAM_MAX_RETRIES: int = Field(
        default=3, ge=1, le=10,
        description="Max review-and-retry cycles per implementation worker.",
    )
    TEAM_WORKER_MAX_STEPS: int = Field(
        default=30, ge=5, le=100,
        description="Max LLM steps per worker before forced stop.",
    )
    TEAM_SCRATCHPAD_DIR: str = Field(
        default=".shadowdev/team/scratchpad",
        description="Shared scratchpad directory for cross-worker knowledge.",
    )

    @model_validator(mode='after')
    def expand_env_refs(self) -> 'Settings':
        """Expand {env:VAR} references in all string config fields."""
        for field_name in type(self).model_fields:
            val = getattr(self, field_name, None)
            if isinstance(val, str) and '{env:' in val:
                object.__setattr__(self, field_name, _expand_env_refs(val))
        return self

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

    @field_validator("SANDBOX_NETWORK")
    @classmethod
    def validate_sandbox_network(cls, v):
        allowed = {"none", "bridge", "host"}
        if v.lower() not in allowed:
            raise ValueError(f"SANDBOX_NETWORK must be one of {allowed}, got '{v}'")
        return v.lower()

    @field_validator("SANDBOX_MEMORY")
    @classmethod
    def validate_sandbox_memory(cls, v):
        import re
        if v and not re.fullmatch(r'\d+[bkmgBKMG]?', v):
            raise ValueError(f"SANDBOX_MEMORY must be a Docker memory string like '512m' or '2g', got '{v}'")
        return v

    @field_validator("SANDBOX_CPUS")
    @classmethod
    def validate_sandbox_cpus(cls, v):
        if v and not str(v).replace('.', '', 1).isdigit():
            raise ValueError(f"SANDBOX_CPUS must be a numeric string like '1.5', got '{v}'")
        return v

    @field_validator("COMPACTION_BUFFER")
    @classmethod
    def validate_compaction_buffer(cls, v, info):
        """Warn if COMPACTION_BUFFER is larger than smallest model context limit."""
        import warnings
        limits = info.data.get("MODEL_CONTEXT_LIMITS", {})
        if limits:
            smallest = min(limits.values())
            if v >= smallest:
                warnings.warn(
                    f"COMPACTION_BUFFER ({v}) >= smallest model context limit ({smallest}). "
                    f"Token-based compaction may never trigger for small models.",
                    stacklevel=2,
                )
        return v

    @field_validator(
        "OLLAMA_MODEL", "OPENAI_MODEL", "OLLAMA_FAST_MODEL", "OPENAI_FAST_MODEL",
        "ANTHROPIC_MODEL", "ANTHROPIC_FAST_MODEL",
        "GOOGLE_MODEL", "GOOGLE_FAST_MODEL",
        "GROQ_MODEL", "GROQ_FAST_MODEL",
        "AZURE_OPENAI_MODEL", "AZURE_OPENAI_FAST_MODEL",
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

ANTHROPIC_API_KEY = _settings.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = _settings.ANTHROPIC_MODEL
ANTHROPIC_FAST_MODEL = _settings.ANTHROPIC_FAST_MODEL

GOOGLE_API_KEY = _settings.GOOGLE_API_KEY
GOOGLE_MODEL = _settings.GOOGLE_MODEL
GOOGLE_FAST_MODEL = _settings.GOOGLE_FAST_MODEL

GROQ_API_KEY = _settings.GROQ_API_KEY
GROQ_MODEL = _settings.GROQ_MODEL
GROQ_FAST_MODEL = _settings.GROQ_FAST_MODEL

AZURE_OPENAI_API_KEY = _settings.AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT = _settings.AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_MODEL = _settings.AZURE_OPENAI_MODEL
AZURE_OPENAI_FAST_MODEL = _settings.AZURE_OPENAI_FAST_MODEL
AZURE_OPENAI_API_VERSION = _settings.AZURE_OPENAI_API_VERSION

VLLM_BASE_URL = _settings.VLLM_BASE_URL
VLLM_MODEL = _settings.VLLM_MODEL
VLLM_FAST_MODEL = _settings.VLLM_FAST_MODEL
VLLM_API_KEY = _settings.VLLM_API_KEY

LLAMACPP_BASE_URL = _settings.LLAMACPP_BASE_URL
LLAMACPP_MODEL = _settings.LLAMACPP_MODEL
LLAMACPP_FAST_MODEL = _settings.LLAMACPP_FAST_MODEL
LLAMACPP_API_KEY = _settings.LLAMACPP_API_KEY

LMSTUDIO_BASE_URL = _settings.LMSTUDIO_BASE_URL
LMSTUDIO_MODEL = _settings.LMSTUDIO_MODEL
LMSTUDIO_FAST_MODEL = _settings.LMSTUDIO_FAST_MODEL
LMSTUDIO_API_KEY = _settings.LMSTUDIO_API_KEY

OPENAI_COMPATIBLE_BASE_URL = _settings.OPENAI_COMPATIBLE_BASE_URL
OPENAI_COMPATIBLE_MODEL = _settings.OPENAI_COMPATIBLE_MODEL
OPENAI_COMPATIBLE_FAST_MODEL = _settings.OPENAI_COMPATIBLE_FAST_MODEL
OPENAI_COMPATIBLE_API_KEY = _settings.OPENAI_COMPATIBLE_API_KEY
OPENAI_COMPATIBLE_NAME = _settings.OPENAI_COMPATIBLE_NAME

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

SANDBOX_ENABLED = _settings.SANDBOX_ENABLED
SANDBOX_IMAGE = _settings.SANDBOX_IMAGE
SANDBOX_NETWORK = _settings.SANDBOX_NETWORK
SANDBOX_MEMORY = _settings.SANDBOX_MEMORY
SANDBOX_CPUS = _settings.SANDBOX_CPUS
SANDBOX_PIDS_LIMIT = _settings.SANDBOX_PIDS_LIMIT
SANDBOX_READONLY = _settings.SANDBOX_READONLY

HOOKS_FILE = _settings.HOOKS_FILE

GITHUB_TOKEN = _settings.GITHUB_TOKEN
GITLAB_TOKEN = _settings.GITLAB_TOKEN
GITLAB_INSTANCE_URL = _settings.GITLAB_INSTANCE_URL

MCP_SERVERS = _settings.MCP_SERVERS

MAX_MESSAGES_BEFORE_SUMMARY = _settings.MAX_MESSAGES_BEFORE_SUMMARY
COMPACTION_BUFFER = _settings.COMPACTION_BUFFER
PRUNE_MINIMUM = _settings.PRUNE_MINIMUM
PRUNE_PROTECT = _settings.PRUNE_PROTECT
MODEL_CONTEXT_LIMITS = _settings.MODEL_CONTEXT_LIMITS
RULES_FILENAMES = _settings.RULES_FILENAMES

THEME = _settings.THEME

REASONING_EFFORT = _settings.REASONING_EFFORT

NOTIFY_ON_COMPLETE = _settings.NOTIFY_ON_COMPLETE

UNDERCOVER_MODE = _settings.UNDERCOVER_MODE

AUTO_DREAM_ENABLED = _settings.AUTO_DREAM_ENABLED

COORDINATOR_MODE = _settings.COORDINATOR_MODE
TEAM_MAX_RETRIES = _settings.TEAM_MAX_RETRIES
TEAM_WORKER_MAX_STEPS = _settings.TEAM_WORKER_MAX_STEPS
TEAM_SCRATCHPAD_DIR = _settings.TEAM_SCRATCHPAD_DIR

ADVISOR_MODEL = _settings.ADVISOR_MODEL

# Vertex AI
VERTEX_AI_PROJECT = _settings.VERTEX_AI_PROJECT
VERTEX_AI_LOCATION = _settings.VERTEX_AI_LOCATION
VERTEX_AI_MODEL = _settings.VERTEX_AI_MODEL
VERTEX_AI_FAST_MODEL = _settings.VERTEX_AI_FAST_MODEL

# GitHub Copilot
GITHUB_COPILOT_API_KEY = _settings.GITHUB_COPILOT_API_KEY
GITHUB_COPILOT_MODEL = _settings.GITHUB_COPILOT_MODEL
GITHUB_COPILOT_FAST_MODEL = _settings.GITHUB_COPILOT_FAST_MODEL

# AWS Bedrock
AWS_REGION = _settings.AWS_REGION
AWS_ACCESS_KEY_ID = _settings.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = _settings.AWS_SECRET_ACCESS_KEY
BEDROCK_MODEL = _settings.BEDROCK_MODEL
BEDROCK_FAST_MODEL = _settings.BEDROCK_FAST_MODEL

# Mistral AI
MISTRAL_API_KEY = _settings.MISTRAL_API_KEY
MISTRAL_MODEL = _settings.MISTRAL_MODEL
MISTRAL_FAST_MODEL = _settings.MISTRAL_FAST_MODEL

# Together AI
TOGETHER_API_KEY = _settings.TOGETHER_API_KEY
TOGETHER_MODEL = _settings.TOGETHER_MODEL
TOGETHER_FAST_MODEL = _settings.TOGETHER_FAST_MODEL

# Fireworks AI
FIREWORKS_API_KEY = _settings.FIREWORKS_API_KEY
FIREWORKS_MODEL = _settings.FIREWORKS_MODEL
FIREWORKS_FAST_MODEL = _settings.FIREWORKS_FAST_MODEL

# DeepSeek
DEEPSEEK_API_KEY = _settings.DEEPSEEK_API_KEY
DEEPSEEK_MODEL = _settings.DEEPSEEK_MODEL
DEEPSEEK_FAST_MODEL = _settings.DEEPSEEK_FAST_MODEL

# Perplexity AI
PERPLEXITY_API_KEY = _settings.PERPLEXITY_API_KEY
PERPLEXITY_MODEL = _settings.PERPLEXITY_MODEL
PERPLEXITY_FAST_MODEL = _settings.PERPLEXITY_FAST_MODEL

# xAI / Grok
XAI_API_KEY = _settings.XAI_API_KEY
XAI_MODEL = _settings.XAI_MODEL
XAI_FAST_MODEL = _settings.XAI_FAST_MODEL
