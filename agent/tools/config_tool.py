"""
Config viewer and editor tool — allows the agent to read and update
configuration settings during a session.

Supported operations:
    get  — read one or all config values
    set  — update a config value for the current session (in-memory only)
    list — show all configurable settings with current values and descriptions
"""
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

import config

logger = logging.getLogger(__name__)

# Settings that CAN be changed at runtime (safe subset — no paths, no secrets)
_MUTABLE_SETTINGS: dict[str, dict[str, Any]] = {
    "ADVISOR_MODEL": {
        "type": str,
        "description": "Advisor model name (e.g. 'claude-haiku-4-5'). Empty = disabled.",
    },
    "UNDERCOVER_MODE": {
        "type": bool,
        "description": "Strip AI codenames from commits/PRs.",
    },
    "NOTIFY_ON_COMPLETE": {
        "type": bool,
        "description": "Send desktop notification on long task completion.",
    },
    "AUTO_DREAM_ENABLED": {
        "type": bool,
        "description": "Enable background memory consolidation every 50 turns.",
    },
    "REASONING_EFFORT": {
        "type": str,
        "description": "LLM reasoning effort: none, low, medium, high.",
        "choices": ["none", "low", "medium", "high"],
    },
    "COORDINATOR_MODE": {
        "type": bool,
        "description": "Enable multi-agent coordinator mode.",
    },
    "TEAM_MAX_RETRIES": {
        "type": int,
        "description": "Max retries for team review loop (1–10).",
    },
    "LOG_LEVEL": {
        "type": str,
        "description": "Logging verbosity: DEBUG, INFO, WARNING, ERROR.",
        "choices": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
}

# Read-only settings visible in 'list' but not editable
_READONLY_SETTINGS: list[str] = [
    "LLM_PROVIDER", "LLM_MODEL", "FAST_MODEL", "WORKSPACE_DIR",
    "MAX_CONTEXT_TOKENS", "TOOL_TIMEOUT", "SANDBOX_ENABLED",
]


class ConfigGetArgs(BaseModel):
    key: str = Field(
        default="",
        description="Config key to read (e.g. 'ADVISOR_MODEL'). Leave empty to show all.",
    )


class ConfigSetArgs(BaseModel):
    key: str = Field(description="Config key to update (must be in mutable settings list).")
    value: str = Field(description="New value as a string (will be coerced to the correct type).")


@tool(args_schema=ConfigGetArgs)
def config_get(key: str = "") -> str:
    """Read one or all configuration values.

    Useful for checking current settings before making changes.
    Use config_list to see all configurable options with descriptions.

    Args:
        key: Config key to read. Empty = show all mutable settings.
    """
    if key:
        key = key.strip().upper()
        value = getattr(config, key, "<not found>")
        info = _MUTABLE_SETTINGS.get(key, {})
        desc = info.get("description", "")
        readonly = key in _READONLY_SETTINGS
        edit_note = " [read-only]" if readonly else " [mutable via config_set]"
        return f"{key} = {value!r}{edit_note}\n{desc}"

    lines = ["Current configuration (mutable settings):"]
    for k, meta in _MUTABLE_SETTINGS.items():
        val = getattr(config, k, "<not set>")
        choices = ""
        if "choices" in meta:
            choices = f" (choices: {', '.join(meta['choices'])})"
        lines.append(f"  {k} = {val!r}  — {meta['description']}{choices}")

    lines.append("\nRead-only settings:")
    for k in _READONLY_SETTINGS:
        val = getattr(config, k, "<not set>")
        lines.append(f"  {k} = {val!r}")

    return "\n".join(lines)


@tool(args_schema=ConfigSetArgs)
def config_set(key: str, value: str) -> str:
    """Update a configuration setting for the current session.

    Changes are in-memory only — they persist for this session but are NOT
    saved to .env or any config file. Restart with the new value to persist.

    Only mutable settings can be changed (use config_get to see the list).

    Args:
        key: Configuration key to update.
        value: New value (will be coerced to the expected type).
    """
    key = key.strip().upper()
    if key not in _MUTABLE_SETTINGS:
        allowed = ", ".join(sorted(_MUTABLE_SETTINGS))
        return (
            f"'{key}' is not a mutable setting.\n"
            f"Mutable settings: {allowed}"
        )

    meta = _MUTABLE_SETTINGS[key]
    expected_type = meta["type"]

    # Coerce value to expected type
    try:
        if expected_type is bool:
            coerced: Any = value.lower() in ("true", "1", "yes", "on")
        elif expected_type is int:
            coerced = int(value)
        else:
            coerced = value.strip()
    except (ValueError, AttributeError) as e:
        return f"Invalid value '{value}' for {key} (expected {expected_type.__name__}): {e}"

    # Validate choices if applicable
    if "choices" in meta and coerced not in meta["choices"]:
        return (
            f"Invalid value '{coerced}' for {key}. "
            f"Valid choices: {', '.join(meta['choices'])}"
        )

    old_value = getattr(config, key, "<not set>")
    setattr(config, key, coerced)
    logger.info("Config updated: %s = %r (was %r)", key, coerced, old_value)

    # Apply side-effects for specific settings
    _apply_side_effects(key, coerced)

    return f"{key} updated: {old_value!r} → {coerced!r} (session only)"


def _apply_side_effects(key: str, value: Any) -> None:
    """Apply immediate side-effects when specific config keys change."""
    if key == "ADVISOR_MODEL":
        try:
            from agent.advisor import set_advisor_model
            set_advisor_model(str(value))
        except Exception:
            pass

    elif key == "LOG_LEVEL":
        import logging as _logging
        level = getattr(_logging, str(value).upper(), None)
        if level is not None:
            logging.getLogger().setLevel(level)


@tool
def config_list() -> str:
    """List all available configuration settings with descriptions and current values.

    Shows which settings are mutable (changeable via config_set) and
    which are read-only (set at startup via environment variables).
    """
    return config_get.invoke({"key": ""})
