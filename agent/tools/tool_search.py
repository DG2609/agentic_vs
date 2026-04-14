"""
Tool search — keyword/fuzzy search over available tools.
Allows the agent to discover tools without knowing exact names.

Registry is populated at graph build time via register_tools().
"""
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# In-memory registry: name -> (description, tool_object)
_TOOL_REGISTRY: dict[str, tuple[str, Any]] = {}


def register_tools(tools: list[Any]) -> None:
    """Populate the registry from a list of LangChain tool objects.

    Called once from graph.py after all core tools are assembled.
    """
    _TOOL_REGISTRY.clear()
    for t in tools:
        desc = (getattr(t, "description", None) or "").strip()
        first_line = desc.split("\n")[0][:300]
        _TOOL_REGISTRY[t.name] = (first_line, t)
    logger.debug("Tool registry populated: %d tools", len(_TOOL_REGISTRY))


class ToolSearchArgs(BaseModel):
    query: str = Field(
        description=(
            "Keywords to search for. Matches against tool names and descriptions. "
            "Examples: 'file read', 'git commit', 'memory search', 'run tests'."
        )
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return (default 10).",
    )


@tool(args_schema=ToolSearchArgs)
def tool_search(query: str, limit: int = 10) -> str:
    """Search available tools by keyword. Returns matching tool names and descriptions.

    Use this when you're unsure what tool to use for a task — search for keywords
    related to what you want to do and the tool will suggest candidates.

    Examples:
    - tool_search("read file") → file_read, batch_read, ...
    - tool_search("git log history") → git_log, git_show, ...
    - tool_search("run tests pytest") → run_tests

    Args:
        query: Keywords to match against tool names and descriptions.
        limit: Max results to return.
    """
    if not _TOOL_REGISTRY:
        return (
            "Tool registry is empty — tools are registered at session start. "
            "If you're in a test environment, call register_tools() first."
        )

    terms = query.lower().split()
    if not terms:
        return "Please provide at least one search keyword."

    # Score each tool: count how many query terms appear in name+description
    scored: list[tuple[int, str, str]] = []
    for name, (desc, _) in _TOOL_REGISTRY.items():
        haystack = (name + " " + desc).lower()
        # Exact name match gets bonus
        score = sum(2 if term in name.lower() else (1 if term in haystack else 0) for term in terms)
        if score > 0:
            scored.append((score, name, desc))

    if not scored:
        # Fall back to listing tools whose name contains any term character
        partial = [
            (1, name, desc)
            for name, (desc, _) in _TOOL_REGISTRY.items()
            if any(c in name for term in terms for c in term if len(c) > 2)
        ]
        if not partial:
            return (
                f"No tools found matching '{query}'.\n"
                f"Use tool_search with broader keywords, or check the tool list via todo_read."
            )
        scored = partial

    scored.sort(key=lambda x: -x[0])
    results = scored[:limit]

    lines = [f"Tools matching '{query}' ({len(results)} of {len(scored)} matches):"]
    for score, name, desc in results:
        lines.append(f"  {name} — {desc}")

    if len(scored) > limit:
        lines.append(f"  ... and {len(scored) - limit} more. Narrow your query or increase limit.")

    return "\n".join(lines)


@tool
def tool_list() -> str:
    """List ALL available tools grouped by category.

    Use tool_search for keyword-based discovery, or tool_list to see everything.
    """
    if not _TOOL_REGISTRY:
        return "Tool registry is empty."

    # Group by common prefixes
    categories: dict[str, list[str]] = {}
    category_prefixes = [
        ("file_", "File Operations"),
        ("git_", "Git"),
        ("github_", "GitHub"),
        ("gitlab_", "GitLab"),
        ("lsp_", "Language Server (LSP)"),
        ("memory_", "Memory"),
        ("skill_", "Skills"),
        ("cron_", "Scheduling"),
        ("snapshot_", "Snapshots"),
        ("chub_", "Context Hub"),
        ("code_", "Code Analysis"),
        ("batch_", "Batch Operations"),
        ("task_", "Subagents"),
        ("todo_", "Task Management"),
        ("plan_", "Planning"),
        ("worker_", "Agent Teams"),
        ("team_", "Agent Teams"),
    ]

    categorized: set[str] = set()
    for prefix, label in category_prefixes:
        group = [n for n in sorted(_TOOL_REGISTRY) if n.startswith(prefix)]
        if group:
            categories[label] = group
            categorized.update(group)

    # Uncategorized
    rest = sorted(n for n in _TOOL_REGISTRY if n not in categorized)
    if rest:
        categories["Other"] = rest

    lines = [f"Available tools ({len(_TOOL_REGISTRY)} total):"]
    for label, names in categories.items():
        lines.append(f"\n{label}:")
        for name in names:
            desc = _TOOL_REGISTRY[name][0][:80]
            lines.append(f"  {name} — {desc}")

    return "\n".join(lines)
