"""
Plugin Registry — discovers ShadowDev tool plugins installed via pip.

Convention: Any pip package that registers tools in the "shadowdev.tools"
entry_point group is automatically loaded when the agent starts.

Plugin authoring quick-start
────────────────────────────
1. Create a package (e.g. shadowdev-plugin-database)
2. In its pyproject.toml:
       [project.entry-points."shadowdev.tools"]
       database = "shadowdev_plugin_database.tools"
3. The module must export:
       __skill_tools__   = [my_tool, ...]     # REQUIRED — list of @tool objects
       __skill_access__  = "read"             # optional: "read" (default) or "write"
       __skill_name__    = "Database"         # optional: display name
       __skill_version__ = "1.0.0"            # optional: version string
       __skill_author__  = "Jane Doe"         # optional: author name
       __skill_description__ = "..."          # optional: one-line description
4. pip install shadowdev-plugin-database

Security note
─────────────
Plugins execute in the same process as the agent with full Python access.
Only install plugins from trusted sources.
"""

import logging
import traceback
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "shadowdev.tools"
_VALID_ACCESS = ("read", "write")


@dataclass
class PluginInfo:
    """Metadata about a discovered plugin (loaded or failed)."""
    name: str
    version: str = ""
    author: str = ""
    description: str = ""
    access: str = "read"
    tools: list = field(default_factory=list)
    source: str = "entrypoint"
    error: str = ""         # non-empty if loading failed


# ── Entry-point discovery ──────────────────────────────────────

def discover_plugins() -> list[PluginInfo]:
    """Discover all installed ShadowDev plugins via importlib entry_points.

    Returns a list of PluginInfo for every registered entry point in the
    "shadowdev.tools" group, including ones that failed to load (with .error set).
    Returns an empty list if the group is not found or discovery fails.
    """
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group=_ENTRY_POINT_GROUP)
    except Exception as e:
        logger.debug("[plugin_registry] entry_points discovery failed: %s", e)
        return []

    return [_load_entry_point(ep) for ep in eps]


def _load_entry_point(ep) -> PluginInfo:
    """Load a single entry point; return PluginInfo with .error on failure."""
    try:
        module = ep.load()
        return _extract_plugin_info(module, name=ep.name)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.warning(
            "[plugin_registry] Failed to load plugin '%s': %s\n%s",
            ep.name, err, traceback.format_exc(),
        )
        return PluginInfo(name=ep.name, error=err)


def _extract_plugin_info(module, name: str) -> PluginInfo:
    """Extract metadata and tools from a successfully imported module."""
    raw_tools = getattr(module, "__skill_tools__", None)
    if raw_tools is None:
        return PluginInfo(name=name, error="missing __skill_tools__")

    if not isinstance(raw_tools, (list, tuple)):
        return PluginInfo(
            name=name,
            error=f"__skill_tools__ must be a list, got {type(raw_tools).__name__}",
        )

    access = getattr(module, "__skill_access__", "read")
    if access not in _VALID_ACCESS:
        logger.warning(
            "[plugin_registry] Plugin '%s' has unknown __skill_access__='%s', defaulting to 'read'",
            name, access,
        )
        access = "read"

    # Filter out invalid tools (LangChain tools need .invoke)
    valid_tools = []
    for t in raw_tools:
        t_name = getattr(t, "name", None) or getattr(t, "__name__", repr(t))
        if not hasattr(t, "invoke"):
            logger.warning(
                "[plugin_registry] Plugin '%s': '%s' missing .invoke — skipped", name, t_name
            )
            continue
        valid_tools.append(t)

    return PluginInfo(
        name=name,
        version=getattr(module, "__skill_version__", ""),
        author=getattr(module, "__skill_author__", ""),
        description=getattr(module, "__skill_description__", ""),
        access=access,
        tools=valid_tools,
    )


# ── Public API ────────────────────────────────────────────────

def get_plugin_tools(existing_names: set | None = None) -> tuple[list, list]:
    """Discover all entry_point plugins and return their tools, deduplicated.

    Args:
        existing_names: Tool names already registered (dedup guard).

    Returns:
        (planner_tools, coder_only_tools) — lists of @tool objects.
        Tools with access="write" go to coder_only; all others go to planner.
    """
    seen: set = set(existing_names or [])
    planner_tools: list = []
    coder_only_tools: list = []
    total_loaded = 0

    for plugin in discover_plugins():
        if plugin.error:
            logger.warning(
                "[plugin_registry] Plugin '%s' skipped (error: %s)", plugin.name, plugin.error
            )
            continue

        accepted: list = []
        for t in plugin.tools:
            t_name = getattr(t, "name", None) or getattr(t, "__name__", repr(t))
            if t_name in seen:
                logger.warning(
                    "[plugin_registry] Plugin '%s': tool '%s' conflicts with existing name — skipped",
                    plugin.name, t_name,
                )
                continue
            accepted.append(t)
            seen.add(t_name)

        if not accepted:
            continue

        if plugin.access == "write":
            coder_only_tools.extend(accepted)
        else:
            planner_tools.extend(accepted)

        total_loaded += len(accepted)
        ver_str = f" v{plugin.version}" if plugin.version else ""
        logger.info(
            "[plugin_registry] Loaded plugin '%s'%s: %d tool(s) (access=%s)",
            plugin.name, ver_str, len(accepted), plugin.access,
        )

    if total_loaded > 0:
        print(
            f"[plugins] Loaded {total_loaded} tool(s) from entry_point plugins "
            f"(group: {_ENTRY_POINT_GROUP!r})"
        )

    return planner_tools, coder_only_tools


def list_plugins() -> list[dict]:
    """Return metadata for all discovered plugins (including failed ones).

    Used by `skill_list` tool to show installed pip plugins alongside file skills.
    """
    result = []
    for p in discover_plugins():
        result.append({
            "name": p.name,
            "version": p.version or "—",
            "author": p.author or "—",
            "description": p.description or "—",
            "access": p.access,
            "tools": [getattr(t, "name", "?") for t in p.tools],
            "tool_count": len(p.tools),
            "status": "error" if p.error else "loaded",
            "error": p.error,
        })
    return result
