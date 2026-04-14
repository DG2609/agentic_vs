"""
Error ID registry — structured, enumerated error codes for ShadowDev.
Inspired by Claude Code's errorIds.ts.

Error IDs allow telemetry, log aggregation, and user-facing error messages
to reference a specific, stable code rather than free-form strings.

Usage:
    from agent.constants.errors import Errors, TelemetrySafeError

    raise TelemetrySafeError(Errors.TOOL_TIMEOUT, "terminal_exec timed out after 120s")
    logger.error("Error [%d]: %s", Errors.TOOL_TIMEOUT, details)

Error ID ranges:
    1–99    Core agent errors
    100–199 Tool execution errors
    200–299 File operation errors
    300–399 Network / external service errors
    400–499 Auth / permission errors
    500–599 Configuration errors
    600–699 LLM provider errors
    700–799 Memory / session errors
    800–899 Team / multi-agent errors
    900–999 Reserved
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class _ErrorRegistry:
    """Namespace of named integer error codes. Prevents duplicate IDs."""

    _registry: dict[int, str] = {}

    def _register(self, code: int, name: str) -> int:
        if code in self._registry:
            raise ValueError(
                f"Duplicate error ID {code}: '{self._registry[code]}' vs '{name}'"
            )
        self._registry[code] = name
        return code

    # ── Core agent (1–99) ─────────────────────────────────────────────────────
    UNKNOWN                             = 1
    CONTEXT_OVERFLOW                    = 2
    COMPACTION_FAILED                   = 3
    SUMMARIZE_FAILED                    = 4
    AGENT_NODE_UNHANDLED                = 5
    DOOM_LOOP_DETECTED                  = 6
    STATE_SERIALIZATION_FAILED          = 7
    SESSION_MEMORY_EXTRACTION_FAILED    = 8
    AUTO_DREAM_FAILED                   = 9
    MICRO_COMPACT_FAILED                = 10
    FORK_SESSION_FAILED                 = 11

    # ── Tool execution (100–199) ──────────────────────────────────────────────
    TOOL_TIMEOUT                        = 100
    TOOL_BLOCKED_BY_HOOK                = 101
    TOOL_PERMISSION_DENIED              = 102
    TOOL_EXECUTION_ERROR                = 103
    TOOL_SCHEMA_VALIDATION_FAILED       = 104
    TOOL_CONCURRENCY_LIMIT              = 105
    TOOL_UNKNOWN                        = 106
    TERMINAL_COMMAND_BLOCKED            = 110
    TERMINAL_SANDBOX_UNAVAILABLE        = 111
    NOTEBOOK_PARSE_ERROR                = 120
    NOTEBOOK_CELL_OUT_OF_RANGE          = 121

    # ── File operations (200–299) ─────────────────────────────────────────────
    FILE_NOT_FOUND                      = 200
    FILE_READ_ERROR                     = 201
    FILE_WRITE_ERROR                    = 202
    FILE_EDIT_NO_MATCH                  = 203
    FILE_PATH_TRAVERSAL                 = 204
    FILE_TOO_LARGE                      = 205
    FILE_DANGEROUS_DOTFILE              = 206
    FILE_BINARY_BLOCKED                 = 207
    GLOB_UNC_PATH_BLOCKED               = 210
    GLOB_LIMIT_EXCEEDED                 = 211
    SNAPSHOT_CREATE_FAILED              = 220
    SNAPSHOT_REVERT_FAILED              = 221

    # ── Network / external services (300–399) ─────────────────────────────────
    WEBFETCH_FAILED                     = 300
    WEBSEARCH_FAILED                    = 301
    MCP_SERVER_UNREACHABLE              = 310
    MCP_TOOL_SCHEMA_INVALID             = 311
    MCP_RESOURCE_NOT_FOUND              = 312
    GITHUB_API_ERROR                    = 320
    GITLAB_API_ERROR                    = 321
    CONTEXT_HUB_FETCH_FAILED            = 330

    # ── Auth / permissions (400–499) ──────────────────────────────────────────
    PERMISSION_DENIED                   = 400
    API_KEY_MISSING                     = 401
    API_KEY_INVALID                     = 402
    FREE_USAGE_LIMIT                    = 403
    RATE_LIMIT                          = 404
    MCP_AUTH_FAILED                     = 410

    # ── Configuration (500–599) ───────────────────────────────────────────────
    CONFIG_INVALID                      = 500
    CONFIG_ENV_VAR_MISSING              = 501
    CONFIG_WORKSPACE_MISSING            = 502
    CONFIG_DATA_DIR_FAILED              = 503
    CRON_INVALID_EXPRESSION             = 510
    CRON_LIMIT_EXCEEDED                 = 511

    # ── LLM provider (600–699) ────────────────────────────────────────────────
    LLM_PROVIDER_UNSUPPORTED            = 600
    LLM_API_ERROR                       = 601
    LLM_TIMEOUT                         = 602
    LLM_CONTEXT_LENGTH_EXCEEDED         = 603
    LLM_OVERLOADED                      = 604
    LLM_CONTENT_FILTERED                = 605
    ADVISOR_FAILED                      = 610
    AUTO_DREAM_LLM_FAILED               = 611

    # ── Memory / session (700–799) ────────────────────────────────────────────
    MEMORY_SAVE_FAILED                  = 700
    MEMORY_SEARCH_FAILED                = 701
    MEMORY_DB_CORRUPT                   = 702
    SESSION_STORE_WRITE_FAILED          = 710
    SESSION_STORE_READ_FAILED           = 711
    SESSION_STORE_DB_CORRUPT            = 712

    # ── Team / multi-agent (800–899) ──────────────────────────────────────────
    WORKER_SPAWN_FAILED                 = 800
    WORKER_MESSAGE_FAILED               = 801
    WORKER_STOPPED_UNEXPECTEDLY         = 802
    REVIEW_LOOP_MAX_RETRIES             = 810
    ORCHESTRATOR_FAILED                 = 811
    TEAM_SCRATCHPAD_WRITE_FAILED        = 820

    def name_of(self, code: int) -> str:
        """Return the name of an error code, or 'UNKNOWN' if not registered."""
        for attr, val in vars(type(self)).items():
            if isinstance(val, int) and val == code:
                return attr
        return "UNKNOWN"

    def __repr__(self) -> str:
        codes = {k: v for k, v in vars(type(self)).items() if isinstance(v, int) and not k.startswith("_")}
        return f"<ErrorRegistry: {len(codes)} codes>"


Errors = _ErrorRegistry()


class TelemetrySafeError(Exception):
    """Exception that carries a structured error code safe for telemetry/logging.

    Wraps the root cause without exposing raw file paths, user data, or secrets
    in the error message string (callers must sanitize those before passing *details*).

    Usage:
        raise TelemetrySafeError(Errors.FILE_NOT_FOUND, "/home/user/.env not found")
        # → message: "[E200] FILE_NOT_FOUND: /home/user/.env not found"
    """

    def __init__(self, code: int, details: str = "", cause: Optional[Exception] = None):
        self.code = code
        self.details = details
        self.cause = cause
        name = Errors.name_of(code)
        super().__init__(f"[E{code}] {name}: {details}")
        if cause:
            self.__cause__ = cause

    @property
    def is_retryable(self) -> bool:
        """Return True for transient errors that can be retried."""
        return self.code in {
            Errors.TOOL_TIMEOUT,
            Errors.LLM_TIMEOUT,
            Errors.LLM_OVERLOADED,
            Errors.RATE_LIMIT,
            Errors.MCP_SERVER_UNREACHABLE,
            Errors.WEBFETCH_FAILED,
            Errors.WEBSEARCH_FAILED,
        }

    @property
    def is_fatal(self) -> bool:
        """Return True for errors that should abort the current operation."""
        return self.code in {
            Errors.FREE_USAGE_LIMIT,
            Errors.API_KEY_MISSING,
            Errors.API_KEY_INVALID,
            Errors.FILE_PATH_TRAVERSAL,
            Errors.TERMINAL_COMMAND_BLOCKED,
        }

    def log(self, log: logging.Logger = logger, level: int = logging.ERROR) -> None:
        """Log this error with structured context."""
        log.log(level, "Error [E%d] %s: %s", self.code, Errors.name_of(self.code), self.details,
                exc_info=self.cause)
