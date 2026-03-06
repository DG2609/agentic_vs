"""
Tools: Agent Skills — invoke, list, and create markdown workflow skills.

Skills are .md files in skills/ (and skills/agents/) that encode expert
workflows, checklists, and agent personas.  When invoked, their body is
injected directly into the LLM's context, optionally with live shell output
(!`cmd`) and user arguments ($ARGUMENTS) substituted in first.

Three tools are exposed here:

    skill_invoke  — run a skill by name, inject its content into context
    skill_list    — discover and list available skills with descriptions
    skill_create  — save a new or updated workflow as a .md skill file
"""

from langchain_core.tools import tool
from agent.skill_engine import (
    invoke_skill,
    discover_skills,
    SKILLS_DIR,
)
from agent.tools.truncation import truncate_output
from models.tool_schemas import (
    SkillInvokeArgs,
    SkillCreateArgs,
    HubSearchArgs,
    SkillInstallArgs,
    SkillRemoveArgs,
)
import re


# ── skill_invoke ─────────────────────────────────────────────

@tool(args_schema=SkillInvokeArgs)
def skill_invoke(name: str, arguments: str = "") -> str:
    """Invoke a markdown workflow skill by name.

    Loads the skill's markdown body, substitutes shell-command blocks
    (!`cmd`) with live output and replaces $ARGUMENTS with the provided
    arguments string, then returns the processed instructions for use
    in the current task.

    If the skill has a `model` frontmatter field, that preference is
    noted in the output so the caller can switch models if needed.

    Args:
        name:      Skill name (from skill_list) or filename stem.
        arguments: Context text injected at $ARGUMENTS in the skill body.

    Returns:
        Processed skill content (markdown), or an error message if not found.
    """
    content, meta = invoke_skill(name, arguments=arguments)

    if meta is None:
        # Error path — content is the error message
        return content

    header_parts = [f"# Skill: {meta.name}"]
    if meta.description:
        header_parts.append(f"_{meta.description}_")
    if meta.model:
        header_parts.append(f"\n> **Suggested model:** `{meta.model}`")
    if meta.subtask:
        header_parts.append("> *(designed as a background subtask)*")

    header = "\n".join(header_parts)
    return truncate_output(f"{header}\n\n---\n\n{content}")


# ── skill_list ───────────────────────────────────────────────

@tool
def skill_list() -> str:
    """List all available agent skills with their descriptions.

    Scans skills/ and skills/agents/ for .md skill files and returns
    a formatted table with each skill's name, category, and description.

    Returns:
        Formatted list of skills, or a message if none are found.
    """
    skills = discover_skills()

    if not skills:
        return (
            "No skills found.\n\n"
            f"Add .md skill files to: {SKILLS_DIR}\n"
            "Each file needs YAML frontmatter with `name` and `description`.\n"
            "Use skill_create() to create a new skill."
        )

    # Group by directory (workflow skills vs agent personas)
    workflow = []
    agents = []
    for s in skills:
        if "agents" in s.meta.source_file.replace("\\", "/"):
            agents.append(s)
        else:
            workflow.append(s)

    lines = ["## Available Skills\n"]

    if workflow:
        lines.append("### Workflow Skills\n")
        for s in sorted(workflow, key=lambda x: x.meta.name):
            desc = s.meta.description or "*(no description)*"
            model_note = f"  ·  model: `{s.meta.model}`" if s.meta.model else ""
            subtask_note = "  ·  subtask" if s.meta.subtask else ""
            lines.append(f"- **{s.meta.name}** — {desc}{model_note}{subtask_note}")

    if agents:
        lines.append("\n### Agent Personas\n")
        for s in sorted(agents, key=lambda x: x.meta.name):
            desc = s.meta.description or "*(no description)*"
            model_note = f"  ·  model: `{s.meta.model}`" if s.meta.model else ""
            lines.append(f"- **{s.meta.name}** — {desc}{model_note}")

    # ── Installed pip plugins ─────────────────────────────────
    try:
        from agent.plugin_registry import list_plugins
        plugins = list_plugins()
    except Exception:
        plugins = []

    if plugins:
        lines.append("\n### Installed Plugins (pip)\n")
        for p in sorted(plugins, key=lambda x: x["name"]):
            status = "⚠️ error" if p["status"] == "error" else "✅"
            desc = p["description"] if p["description"] != "—" else "*(no description)*"
            ver = f"  v{p['version']}" if p["version"] != "—" else ""
            author = f"  by {p['author']}" if p["author"] != "—" else ""
            tool_names = ", ".join(f"`{t}`" for t in p["tools"][:5])
            if len(p["tools"]) > 5:
                tool_names += f" +{len(p['tools']) - 5} more"
            access = f"  access={p['access']}" if p["status"] == "loaded" else ""
            err = f"  error: {p['error']}" if p["error"] else ""
            lines.append(
                f"- {status} **{p['name']}**{ver}{author} — {desc}{access}"
                + (f"\n  Tools: {tool_names}" if tool_names else "")
                + (f"\n  ⚠️ {err}" if err else "")
            )

    lines.append(
        f"\n_Use `skill_invoke(name=...)` to load a skill into context._\n"
        f"_Install plugins with `pip install shadowdev-plugin-<name>`._"
    )
    return truncate_output("\n".join(lines))


# ── skill_create ─────────────────────────────────────────────

@tool(args_schema=SkillCreateArgs)
def skill_create(
    name: str,
    description: str,
    content: str,
    model: str = "",
    subtask: bool = False,
) -> str:
    """Create or overwrite a markdown workflow skill.

    Writes a new .md file to skills/<name>.md with proper YAML frontmatter
    and the provided markdown body.  Use $ARGUMENTS in the body as a
    placeholder for user-provided context at invocation time.

    Overwrites an existing skill with the same name after confirmation
    (the return message will indicate what happened).

    Args:
        name:        Skill name slug (e.g. 'code-review'). Saved as skills/<name>.md.
                     Only alphanumeric characters, hyphens, and underscores are kept;
                     all other characters are replaced with hyphens.
        description: One-line description shown in skill_list().
        content:     Markdown body (workflow instructions, checklists, etc.).
        model:       Optional model override for this skill (e.g. 'claude-opus-4-6').
        subtask:     Mark as a background subtask skill.

    Returns:
        Confirmation message with the skill path, or error if write fails.
    """
    # Sanitise name → valid filename (keep alphanumeric, hyphens, underscores)
    safe_name = re.sub(r"[^\w\-]", "-", name.strip()).strip("-")
    if not safe_name:
        return "Error: skill name produces an empty filename after sanitisation."

    skill_path = SKILLS_DIR / f"{safe_name}.md"
    existed = skill_path.exists()

    # Build frontmatter
    fm_lines = ["---", f"name: {safe_name}", f"description: {description}"]
    if model:
        fm_lines.append(f"model: {model}")
    if subtask:
        fm_lines.append("subtask: true")
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    full_text = f"{frontmatter}\n\n{content.lstrip()}"

    try:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(full_text, encoding="utf-8")
    except OSError as e:
        return f"Error writing skill '{safe_name}': {e}"

    action = "Updated" if existed else "Created"
    return (
        f"{action} skill **{safe_name}**\n"
        f"Path: `{skill_path}`\n\n"
        f"Use `skill_invoke(name='{safe_name}')` to load it."
    )


# ── hub_search ────────────────────────────────────────────────

@tool(args_schema=HubSearchArgs)
def hub_search(query: str = "", category: str = "", tag: str = "") -> str:
    """Search the Skill Hub index for community skills.

    Fetches the remote hub index and filters by an optional keyword query,
    category, and/or tag.  Returns a formatted list of matching skills with
    names, versions, descriptions, and install instructions.

    Leave all parameters empty to list every available skill.

    Args:
        query:    Keyword to match in name, description, or tags.
        category: Exact category filter (e.g. 'devops', 'testing').
        tag:      Exact tag filter (e.g. 'deploy', 'security').

    Returns:
        Formatted list of matching hub skills, or an error message.
    """
    try:
        from agent.skill_hub import fetch_index
    except ImportError:
        return "Error: agent.skill_hub module not available."

    try:
        index = fetch_index()
    except RuntimeError as e:
        return f"Hub error: {e}"

    results = index.search(query=query, category=category, tag=tag)

    if not results:
        filters = []
        if query:
            filters.append(f"query={query!r}")
        if category:
            filters.append(f"category={category!r}")
        if tag:
            filters.append(f"tag={tag!r}")
        filter_str = ", ".join(filters) if filters else "no filters"
        return (
            f"No hub skills found ({filter_str}).\n\n"
            f"Available categories: {', '.join(index.categories) or 'none'}\n"
            "Try `hub_search()` with no arguments to list all skills."
        )

    lines = [f"## Skill Hub — {len(results)} result(s)\n"]
    for s in results:
        name = s.get("name", "?")
        ver = s.get("version", "")
        cat = s.get("category", "")
        desc = s.get("description", "")
        tags = ", ".join(f"`{t}`" for t in s.get("tags", []))
        author = s.get("author", "")
        skill_type = s.get("type", "markdown")

        meta_parts = []
        if ver:
            meta_parts.append(f"v{ver}")
        if cat:
            meta_parts.append(cat)
        if author:
            meta_parts.append(f"by {author}")
        meta_parts.append(skill_type)
        meta = "  ·  ".join(meta_parts)

        lines.append(f"- **{name}** ({meta})")
        if desc:
            lines.append(f"  {desc}")
        if tags:
            lines.append(f"  Tags: {tags}")
        lines.append(f"  Install: `skill_install(name='{name}')`")

    lines.append(
        f"\n_Use `skill_install(name='<name>')` to install a skill._\n"
        f"_Use `skill_list()` to see locally installed skills._"
    )
    return truncate_output("\n".join(lines))


# ── skill_install ─────────────────────────────────────────────

@tool(args_schema=SkillInstallArgs)
def skill_install(name: str, url: str = "", overwrite: bool = False) -> str:
    """Install a skill from the Skill Hub or a direct URL.

    Downloads and installs either a markdown workflow skill (.md) or a Python
    tool plugin (.py) from the community Skill Hub index or a raw URL.

    After installation:
      - Markdown skills are available immediately via skill_invoke().
      - Plugin tools require restarting the agent to take effect.

    Args:
        name:      Skill name to look up in the hub (if url not given),
                   or the local filename stem for a direct URL install.
        url:       Optional direct URL to a .md or .py skill file.
                   Bypasses the hub index lookup.
        overwrite: Replace an already-installed skill with the same name.

    Returns:
        Confirmation with the installed path and sha256, or an error message.
    """
    try:
        from agent.skill_hub import install_skill as _install
    except ImportError:
        return "Error: agent.skill_hub module not available."

    try:
        result = _install(name=name, url=url or None, overwrite=overwrite)
    except (RuntimeError, ValueError) as e:
        return f"Install failed: {e}"

    status = result.get("status", "installed")
    skill_path = result.get("path", "")
    skill_type = result.get("type", "")
    version = result.get("version", "unknown")
    sha = result.get("sha256", "")

    lines = [
        f"Skill **{name}** {status} successfully.",
        f"Path: `{skill_path}`",
        f"Type: {skill_type}  ·  Version: {version}  ·  sha256: `{sha}`",
    ]
    if skill_type == "plugin":
        lines.append(
            "\n> **Note:** Plugin tools require restarting the agent to take effect."
        )
    else:
        lines.append(f"\nUse `skill_invoke(name='{name}')` to load it.")

    return "\n".join(lines)


# ── skill_remove ──────────────────────────────────────────────

@tool(args_schema=SkillRemoveArgs)
def skill_remove(name: str) -> str:
    """Remove a locally installed skill (markdown or plugin).

    Deletes the skill file(s) from skills/ or skills/_tools/.
    The change takes effect immediately for markdown skills.
    Removing a plugin tool requires restarting the agent.

    Args:
        name: Name of the skill to remove.

    Returns:
        Confirmation message, or an error if the skill was not found.
    """
    try:
        from agent.skill_hub import remove_skill as _remove
    except ImportError:
        return "Error: agent.skill_hub module not available."

    try:
        result = _remove(name)
    except RuntimeError as e:
        return f"Remove failed: {e}"

    removed = result.get("removed", [])
    paths = "\n".join(f"  - `{p}`" for p in removed)
    return f"Skill **{name}** removed.\n\nDeleted files:\n{paths}"
