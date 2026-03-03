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
from models.tool_schemas import SkillInvokeArgs, SkillCreateArgs
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

    lines.append(
        f"\n_Use `skill_invoke(name=...)` to load a skill into context._"
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
