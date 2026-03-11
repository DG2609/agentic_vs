# Skills Guide

ShadowDev has three complementary systems for extending agent capabilities: **Markdown Workflow Skills**, **Python Tool Plugins**, and the **Skill Hub**.

## Markdown Workflow Skills

Markdown skills are `.md` files that define structured workflows the agent can load into its context. They live in the `skills/` directory.

### Directory Layout

```
skills/
  commit.md              # Workflow skills
  code-review.md
  security-audit.md
  refactor.md
  agents/
    senior-dev.md         # Agent personas
  _tools/
    example_skill.py      # Python tool plugins (see below)
```

### Frontmatter

Each skill file starts with YAML frontmatter:

```markdown
---
name: commit
description: Guided git commit workflow
model: gpt-4o          # optional: suggested model override
subtask: false          # optional: run as background subtask
version: 1.0.0
---

## Steps

1. Run `git status` to see changes
2. Review the diff with `git diff`
3. Stage files with `git add`
4. Write a descriptive commit message
5. Commit with `git commit`
```

### Shell Command Injection

Use `!` backtick syntax to inject live shell output into the skill:

```markdown
## Current Status

!`git status --short`

## Recent Commits

!`git log --oneline -5`
```

When the skill is invoked, these commands run and their output replaces the `!` lines. The `$ARGUMENTS` placeholder is substituted **before** shell commands execute, preventing injection.

### `$ARGUMENTS` Substitution

The caller can pass arguments when invoking a skill:

```
skill_invoke(name="code-review", arguments="src/auth.py")
```

Inside the skill file, `$ARGUMENTS` is replaced with the provided value:

```markdown
## Review Target

Reviewing: $ARGUMENTS

!`cat $ARGUMENTS | head -50`
```

### Using Skills

```
# List available skills
skill_list()

# Invoke a skill
skill_invoke(name="commit", arguments="feat: add auth module")

# Create a new skill
skill_create(
  name="deploy",
  description="Deploy to production",
  content="---\nname: deploy\n---\n\n## Steps\n1. Run tests\n2. Build\n3. Deploy"
)
```

---

## Python Tool Plugins

Python plugins add new LangChain tools to the agent. They live in `skills/_tools/`.

### Plugin Structure

```python
# skills/_tools/my_plugin.py
from langchain_core.tools import tool

@tool
def my_custom_tool(query: str) -> str:
    """Description of what this tool does."""
    return f"Result for: {query}"

# Required: list of tool objects
__skill_tools__ = [my_custom_tool]

# Optional metadata
__skill_access__ = "read"     # "read" = Planner+Coder, "write" = Coder only
__skill_name__ = "my-plugin"
__skill_version__ = "1.0.0"
```

### Plugin API

| Export | Required | Description |
|--------|----------|-------------|
| `__skill_tools__` | Yes | List of LangChain tool objects |
| `__skill_access__` | No | `"read"` (default) or `"write"` |
| `__skill_name__` | No | Human-readable name |
| `__skill_version__` | No | Version string |

### Important Notes

- Tools are validated using `hasattr(t, "invoke")` (not `callable()`, which does not work with LangChain tools in langchain-core 0.3+)
- Plugins that fail to load (syntax error, missing `__skill_tools__`, duplicate names) are skipped with a warning
- File names starting with `_` are skipped (e.g., `_helpers.py`)

---

## Plugin Registry (pip install)

Third-party plugins can be distributed as pip packages and auto-discovered via Python entry points.

### Authoring a Plugin Package

```python
# pyproject.toml
[project]
name = "shadowdev-plugin-mytools"
version = "1.0.0"

[project.entry-points."shadowdev.tools"]
mytools = "shadowdev_plugin_mytools"
```

```python
# shadowdev_plugin_mytools/__init__.py
from langchain_core.tools import tool

@tool
def custom_analyzer(path: str) -> str:
    """Analyze a file with custom logic."""
    return "analysis result"

__skill_tools__ = [custom_analyzer]
__skill_access__ = "read"
__skill_name__ = "mytools"
__skill_version__ = "1.0.0"
__skill_author__ = "Your Name"
__skill_description__ = "Custom analysis tools"
```

### Installing

```bash
pip install shadowdev-plugin-mytools
```

Plugins are discovered automatically at startup. Use `skill_list()` to see installed plugins.

### Conventions

- Package name: `shadowdev-plugin-<name>`
- Entry point group: `"shadowdev.tools"`
- Module must export `__skill_tools__`
- Tools are deduplicated against core tools (no name collisions)

---

## Skill Hub

The Skill Hub is a community registry for discovering and installing skills.

### Searching

```
hub_search(query="deploy")
hub_search(category="devops")
hub_search(tag="docker")
```

### Installing

```
# From the Hub index
skill_install(name="deploy-fly")

# From a direct URL
skill_install(url="https://example.com/skills/my-skill.md")
```

### Removing

```
skill_remove(name="deploy-fly")
```

### Listing Installed

```
skill_list()
```

Shows three sections:
1. **Markdown Skills** -- from `skills/` directory
2. **Python Plugins** -- from `skills/_tools/`
3. **Installed Plugins** -- from pip packages

### Safety

- Markdown skills are plain text files with no code execution beyond `!` shell commands
- Python plugins from the Hub are reviewed before listing
- Size limit: 512KB per downloaded skill
- Name validation: alphanumeric + hyphens only

### Self-Hosted Registry

Set `HUB_INDEX_URL` to point to your own skill index:

```ini
HUB_INDEX_URL=https://internal.company.com/shadowdev/skills.json
```

The index is a JSON file listing available skills with name, description, category, tags, version, author, URL, and type (markdown or plugin).
