"""
Pydantic schemas for tool arguments.

These schemas are passed to LangChain's @tool(args_schema=...) to give
the LLM strict type information, constraints, and field descriptions.
This dramatically reduces hallucinated/wrong tool arguments.

Key benefits:
- LLM sees exact field types + constraints in the function schema
- Pydantic validates BEFORE tool executes → clear error instead of silent bug
- Field descriptions guide LLM on correct usage
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional


# ═══════════════════════════════════════════════════════════
# File Operations
# ═══════════════════════════════════════════════════════════

class FileReadArgs(BaseModel):
    """Arguments for reading a file."""
    file_path: str = Field(
        description="Path to the file. Can be absolute or relative to workspace root."
    )
    start_line: int = Field(
        default=0,
        ge=0,
        description="Start line (1-indexed). 0 = read from beginning."
    )
    end_line: int = Field(
        default=0,
        ge=0,
        description="End line (1-indexed, inclusive). 0 = read to end."
    )

    @field_validator("end_line")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_line", 0)
        if v > 0 and start > 0 and v < start:
            raise ValueError(f"end_line ({v}) must be >= start_line ({start})")
        return v


class FileWriteArgs(BaseModel):
    """Arguments for writing a file."""
    file_path: str = Field(
        description="Path to the file. Relative paths resolve from workspace."
    )
    content: str = Field(
        description="Complete content to write. WARNING: overwrites entire file. Use file_edit for partial changes."
    )
    create_dirs: bool = Field(
        default=True,
        description="Create parent directories if they don't exist."
    )


class FileEditArgs(BaseModel):
    """Arguments for editing a file with fuzzy matching."""
    file_path: str = Field(
        description="Path to the file to edit."
    )
    old_string: str = Field(
        description="The text to find and replace. Must be unique in the file. "
        "Include 3+ context lines before and after the target text for precision. "
        "Minor whitespace differences are tolerated via fuzzy matching."
    )
    new_string: str = Field(
        description="The replacement text. Must be the complete replacement including context."
    )

    @field_validator("old_string")
    @classmethod
    def old_string_not_empty(cls, v):
        if not v.strip():
            raise ValueError("old_string cannot be empty")
        return v


class FileListArgs(BaseModel):
    """Arguments for listing directory contents."""
    directory: str = Field(
        default="",
        description="Directory to list. Empty = workspace root."
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum recursion depth (1-10). Default 3."
    )
    show_size: bool = Field(
        default=False,
        description="Show file sizes in the listing."
    )


class GlobSearchArgs(BaseModel):
    """Arguments for glob pattern file search."""
    pattern: str = Field(
        description="Glob pattern. Use '**' for recursive, e.g. '**/*.py' for all Python files."
    )
    directory: str = Field(
        default="",
        description="Root directory to search. Empty = workspace root."
    )


# ═══════════════════════════════════════════════════════════
# Code Search
# ═══════════════════════════════════════════════════════════

class CodeSearchArgs(BaseModel):
    """Arguments for searching code across files."""
    query: str = Field(
        description="Search pattern — can be a keyword, function name, regex, or error message. "
        "Supports regex syntax."
    )
    directory: str = Field(
        default="",
        description="Directory to search in. Empty = workspace root."
    )
    file_pattern: str = Field(
        default="*",
        description="Glob pattern to filter files, e.g. '*.py', '*.ts', '*.c'. Use '*' for all files."
    )
    max_results: int = Field(
        default=30,
        ge=1,
        le=200,
        description="Maximum number of matching lines to return (1-200)."
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Lines of context before/after each match (0-10)."
    )
    case_sensitive: bool = Field(
        default=False,
        description="Case-sensitive search. Default is case-insensitive."
    )


class GrepSearchArgs(BaseModel):
    """Arguments for searching within a single file."""
    keyword: str = Field(
        description="Keyword or regex pattern to search for in the file."
    )
    file_path: str = Field(
        description="Path to the specific file to search in."
    )
    context_lines: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Lines of context above and below each match (0-10)."
    )


class BatchReadArgs(BaseModel):
    """Arguments for reading multiple files at once."""
    file_paths: list[str] = Field(
        description="List of file paths to read (max 10). "
        "Use this for reading several related files in one call."
    )

    @field_validator("file_paths")
    @classmethod
    def validate_paths(cls, v):
        if not v:
            raise ValueError("Must provide at least one file path")
        if len(v) > 10:
            raise ValueError(f"Maximum 10 files per batch, got {len(v)}")
        return v


# ═══════════════════════════════════════════════════════════
# Terminal
# ═══════════════════════════════════════════════════════════

class TerminalExecArgs(BaseModel):
    """Arguments for executing a shell command."""
    command: str = Field(
        description="The shell command to execute. Runs in workspace directory by default."
    )
    cwd: str = Field(
        default="",
        description="Working directory for the command. Empty = workspace root."
    )
    timeout: int = Field(
        default=0,
        ge=0,
        le=300,
        description="Max execution time in seconds (0 = default 30s, max 300s)."
    )


# ═══════════════════════════════════════════════════════════
# Code Analyzer
# ═══════════════════════════════════════════════════════════

class CodeAnalyzeArgs(BaseModel):
    """Arguments for analyzing code structure."""
    file_path: str = Field(
        description="Path to the source file to analyze. "
        "Supports: Python, JavaScript/TypeScript, C/C++, Java, Matlab."
    )


# ═══════════════════════════════════════════════════════════
# Semantic Search
# ═══════════════════════════════════════════════════════════

class SemanticSearchArgs(BaseModel):
    """Arguments for vector-based semantic code search."""
    query: str = Field(
        description="Natural language description of what you're looking for. "
        "e.g. 'function that handles user authentication' or 'database connection setup'."
    )
    n_results: int = Field(
        default=8,
        ge=1,
        le=30,
        description="Number of code chunks to return (1-30)."
    )
    file_filter: str = Field(
        default="",
        description="Filter by file extension, e.g. '.py', '.ts'. Empty = all files."
    )
    lang_filter: str = Field(
        default="",
        description="Filter by language, e.g. 'python', 'javascript'. Empty = all languages."
    )


class IndexCodebaseArgs(BaseModel):
    """Arguments for indexing the codebase for semantic search."""
    force: bool = Field(
        default=False,
        description="If True, re-index all files even if unchanged. "
        "Use False (default) for incremental indexing. "
        "Run this tool before using semantic_search."
    )


# ═══════════════════════════════════════════════════════════
# LSP Operations
# ═══════════════════════════════════════════════════════════

class LSPPositionArgs(BaseModel):
    """Arguments for LSP operations that need a position (definition, references, hover)."""
    file_path: str = Field(
        description="Path to the source file."
    )
    line: int = Field(
        ge=0,
        description="Line number (0-indexed). Use file_read first to find the correct line."
    )
    col: int = Field(
        ge=0,
        description="Column number (0-indexed). Position within the line where the symbol is."
    )


class LSPFileArgs(BaseModel):
    """Arguments for LSP operations that need only a file path (symbols, diagnostics)."""
    file_path: str = Field(
        description="Path to the source file to analyze."
    )


# ═══════════════════════════════════════════════════════════
# Web
# ═══════════════════════════════════════════════════════════

class WebFetchArgs(BaseModel):
    """Arguments for fetching a web page."""
    url: str = Field(
        description="Full URL to fetch, including https://."
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


# ═══════════════════════════════════════════════════════════
# Git Operations
# ═══════════════════════════════════════════════════════════

class GitDiffArgs(BaseModel):
    """Arguments for viewing git diffs."""
    file_path: str = Field(
        default="",
        description="Specific file to diff. Empty = diff all changed files."
    )
    staged: bool = Field(
        default=False,
        description="Show staged changes (git diff --staged). False = show unstaged changes."
    )
    base: str = Field(
        default="",
        description="Compare against this commit/branch/tag instead of working tree. "
                    "e.g. 'HEAD~1', 'main', 'abc1234'."
    )


class GitLogArgs(BaseModel):
    """Arguments for viewing commit history."""
    n: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Number of commits to show (1-100). Default 15."
    )
    file_path: str = Field(
        default="",
        description="Limit to commits that touched this file."
    )
    branch: str = Field(
        default="",
        description="Show log for this branch or ref. Empty = current branch."
    )
    graph: bool = Field(
        default=False,
        description="Show branch graph. Useful for understanding merge history."
    )


class GitAddArgs(BaseModel):
    """Arguments for staging files."""
    paths: list[str] = Field(
        description="Files or patterns to stage. Use ['.'] to stage all changes. "
                    "e.g. ['src/main.py', 'tests/'] or ['.']"
    )

    @field_validator("paths")
    @classmethod
    def paths_not_empty(cls, v):
        if not v:
            raise ValueError("Must provide at least one path to stage")
        return v


class GitCommitArgs(BaseModel):
    """Arguments for creating a commit."""
    message: str = Field(
        description="Commit message. Be descriptive — summarize WHAT changed and WHY. "
                    "e.g. 'Fix null pointer in UserService.get_profile when user has no avatar'"
    )

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Commit message cannot be empty")
        return v.strip()


class GitBranchArgs(BaseModel):
    """Arguments for branch management."""
    action: str = Field(
        description="Action to perform: 'list' | 'create' | 'switch' | 'delete'"
    )
    name: str = Field(
        default="",
        description="Branch name. Required for create/switch/delete."
    )
    from_ref: str = Field(
        default="",
        description="Create new branch from this ref (commit hash, tag, or branch). "
                    "Empty = current HEAD."
    )

    @field_validator("action")
    @classmethod
    def valid_action(cls, v):
        valid = {"list", "create", "switch", "delete"}
        if v not in valid:
            raise ValueError(f"action must be one of: {sorted(valid)}")
        return v


class GitStashArgs(BaseModel):
    """Arguments for stash management."""
    action: str = Field(
        description="Action: 'save' | 'pop' | 'list' | 'drop'"
    )
    message: str = Field(
        default="",
        description="Description for the stash entry (only used with 'save')."
    )
    index: int = Field(
        default=0,
        ge=0,
        description="Stash index for 'pop'/'drop'. 0 = most recent stash."
    )

    @field_validator("action")
    @classmethod
    def valid_action(cls, v):
        valid = {"save", "pop", "list", "drop"}
        if v not in valid:
            raise ValueError(f"action must be one of: {sorted(valid)}")
        return v


class GitShowArgs(BaseModel):
    """Arguments for showing a commit."""
    ref: str = Field(
        default="HEAD",
        description="Commit ref to show: hash, branch, tag, or relative ref like 'HEAD~2'. "
                    "Default: HEAD (latest commit)."
    )
    file_path: str = Field(
        default="",
        description="Limit output to changes in this specific file."
    )


class GitBlameArgs(BaseModel):
    """Arguments for git blame."""
    file_path: str = Field(
        description="File to annotate with authorship information."
    )
    start_line: int = Field(
        default=0,
        ge=0,
        description="First line to show (1-indexed). 0 = from beginning."
    )
    end_line: int = Field(
        default=0,
        ge=0,
        description="Last line to show (1-indexed, inclusive). 0 = to end of file."
    )

    @field_validator("end_line")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_line", 0)
        if v > 0 and start > 0 and v < start:
            raise ValueError(f"end_line ({v}) must be >= start_line ({start})")
        return v


# ── Git remote operations ────────────────────────────────────

class GitPushArgs(BaseModel):
    """Arguments for git push."""
    remote: str = Field(
        default="origin",
        description="Remote name to push to. Default: 'origin'."
    )
    branch: str = Field(
        default="",
        description="Branch to push. Empty = current branch."
    )
    force: bool = Field(
        default=False,
        description="Force push. WARNING: destructive — rewrites remote history. Only use after rebase."
    )
    set_upstream: bool = Field(
        default=False,
        description="Set upstream tracking (-u flag). Use when pushing a new branch for the first time."
    )


class GitPullArgs(BaseModel):
    """Arguments for git pull."""
    remote: str = Field(
        default="origin",
        description="Remote name to pull from. Default: 'origin'."
    )
    branch: str = Field(
        default="",
        description="Branch to pull. Empty = current branch's upstream."
    )
    rebase: bool = Field(
        default=False,
        description="Rebase instead of merge (--rebase). Produces cleaner linear history."
    )


class GitFetchArgs(BaseModel):
    """Arguments for git fetch."""
    remote: str = Field(
        default="",
        description="Remote to fetch from. Empty = all remotes."
    )
    prune: bool = Field(
        default=True,
        description="Remove tracking branches that no longer exist on remote (--prune). Default True."
    )


class GitMergeArgs(BaseModel):
    """Arguments for git merge."""
    branch: str = Field(
        description="Branch (or ref) to merge into the current branch."
    )
    no_ff: bool = Field(
        default=True,
        description="Always create a merge commit (--no-ff). Default True for clear history."
    )
    message: str = Field(
        default="",
        description="Custom merge commit message. Empty = auto-generated by git."
    )

    @field_validator("branch")
    @classmethod
    def branch_not_empty(cls, v):
        if not v.strip():
            raise ValueError("branch cannot be empty")
        return v.strip()


# ═══════════════════════════════════════════════════════════
# Atomic multi-file edit
# ═══════════════════════════════════════════════════════════

class SingleEditItem(BaseModel):
    """One edit operation within a batch."""
    file_path: str = Field(description="Path to the file to edit.")
    old_string: str = Field(
        description="The text to find (same matching rules as file_edit — fuzzy aware)."
    )
    new_string: str = Field(description="The replacement text.")

    @field_validator("old_string")
    @classmethod
    def old_not_empty(cls, v):
        if not v.strip():
            raise ValueError("old_string cannot be empty")
        return v


class FileEditBatchArgs(BaseModel):
    """Arguments for atomic multi-file editing."""
    edits: list[SingleEditItem] = Field(
        description=(
            "List of edit operations to apply atomically. "
            "All edits succeed or none are written. "
            "Maximum 20 edits per batch."
        )
    )

    @field_validator("edits")
    @classmethod
    def validate_edits(cls, v):
        if not v:
            raise ValueError("Must provide at least one edit")
        if len(v) > 20:
            raise ValueError(f"Maximum 20 edits per batch, got {len(v)}")
        return v


# ═══════════════════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════════════════

class RunTestsArgs(BaseModel):
    """Arguments for running tests."""
    path: str = Field(
        default="",
        description="Sub-directory or specific test file to run. Empty = workspace root."
    )
    framework: str = Field(
        default="auto",
        description=(
            "Test framework: 'auto' (detect), 'pytest', 'jest', 'vitest', "
            "'cargo', 'go', 'make'. Default: auto."
        )
    )
    pattern: str = Field(
        default="",
        description=(
            "Test filter/pattern. For pytest: '-k pattern'. For jest: '--testNamePattern'. "
            "Empty = run all tests."
        )
    )
    timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        description="Max execution time in seconds (5-300). Default 60."
    )

    @field_validator("framework")
    @classmethod
    def valid_framework(cls, v):
        valid = {"auto", "pytest", "jest", "vitest", "cargo", "go", "make"}
        if v not in valid:
            raise ValueError(f"framework must be one of: {sorted(valid)}")
        return v


# ═══════════════════════════════════════════════════════════
# Persistent Memory
# ═══════════════════════════════════════════════════════════

class MemorySaveArgs(BaseModel):
    """Arguments for saving a memory entry."""
    key: str = Field(description="Unique identifier for this memory. Use descriptive names.")
    value: str = Field(description="The information to remember.")
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for categorization, e.g. ['architecture', 'bug', 'pattern']."
    )

    @field_validator("key")
    @classmethod
    def key_not_empty(cls, v):
        if not v.strip():
            raise ValueError("key cannot be empty")
        return v.strip()


class MemorySearchArgs(BaseModel):
    """Arguments for searching memory entries."""
    query: str = Field(description="Search query — matches against key, value, and tags.")
    n_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return (1-20)."
    )


class MemoryDeleteArgs(BaseModel):
    """Arguments for deleting a memory entry."""
    key: str = Field(description="Key of the memory entry to delete.")


class MemoryListArgs(BaseModel):
    """Arguments for listing memory entries."""
    tag: str = Field(
        default="",
        description="Filter by tag. Empty = list all entries."
    )


# ═══════════════════════════════════════════════════════════
# Code Quality
# ═══════════════════════════════════════════════════════════

class CodeQualityArgs(BaseModel):
    """Arguments for code quality analysis."""
    file_path: str = Field(
        description=(
            "Path to the source file to analyze. "
            "Currently supports Python files. Other languages get basic stats."
        )
    )
    include_todos: bool = Field(
        default=True,
        description="Include TODO/FIXME/HACK comment locations in the report."
    )


# ═══════════════════════════════════════════════════════════
# Dependency Graph
# ═══════════════════════════════════════════════════════════

class DepGraphArgs(BaseModel):
    """Arguments for dependency graph analysis."""
    file_path: str = Field(
        description="Entry-point Python file to analyze."
    )
    max_depth: int = Field(
        default=2,
        ge=1,
        le=5,
        description="How many import levels to follow (1-5). Default 2."
    )
    show_stdlib: bool = Field(
        default=False,
        description="Include stdlib imports (os, sys, etc.) in the graph. Default False."
    )


# ═══════════════════════════════════════════════════════════
# Auto-context builder
# ═══════════════════════════════════════════════════════════

class ContextBuildArgs(BaseModel):
    """Arguments for auto-context building."""
    description: str = Field(
        description=(
            "Natural language description of the task or question. "
            "e.g. 'fix the login authentication bug' or 'add rate limiting to the API'."
        )
    )
    max_files: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Maximum number of files to include in context (1-30). Default 10."
    )
    include_deps: bool = Field(
        default=True,
        description="Follow Python import dependencies from matched files. Default True."
    )

    @field_validator("description")
    @classmethod
    def desc_not_empty(cls, v):
        if not v.strip():
            raise ValueError("description cannot be empty")
        return v.strip()


# ═══════════════════════════════════════════════════════════
# Agent Skills (Markdown workflow skills)
# ═══════════════════════════════════════════════════════════

class SkillInvokeArgs(BaseModel):
    """Arguments for invoking a skill."""
    name: str = Field(
        description=(
            "Skill name to invoke. Match against skill names from skill_list(). "
            "Accepts the name, filename stem, or kebab/underscore variants."
        )
    )
    arguments: str = Field(
        default="",
        description=(
            "User context passed to the skill. Replaces $ARGUMENTS placeholder "
            "inside the skill body. Leave empty if the skill doesn't use it."
        )
    )

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()


class SkillCreateArgs(BaseModel):
    """Arguments for creating a new skill file."""
    name: str = Field(
        description=(
            "Skill name (slug-style, e.g. 'code-review'). Used as the filename: "
            "skills/<name>.md. Avoid spaces — use hyphens."
        )
    )
    description: str = Field(
        description="One-line description shown in skill_list() output."
    )
    content: str = Field(
        description=(
            "Full markdown body of the skill (everything after the frontmatter). "
            "Use $ARGUMENTS as a placeholder for user-provided input."
        )
    )
    model: str = Field(
        default="",
        description=(
            "Suggested model override for this skill, e.g. 'claude-opus-4-6'. "
            "Leave empty to inherit the current session model."
        )
    )
    subtask: bool = Field(
        default=False,
        description="Mark as a background subtask skill. Default False."
    )

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()

    @field_validator("description")
    @classmethod
    def desc_not_empty(cls, v):
        if not v.strip():
            raise ValueError("description cannot be empty")
        return v.strip()


# ═══════════════════════════════════════════════════════════
# GitHub Integration
# ═══════════════════════════════════════════════════════════

_REPO_DESC = (
    "GitHub repo as 'owner/repo' (e.g. 'microsoft/vscode'). "
    "Leave empty to auto-detect from git remote origin."
)


class GithubListIssuesArgs(BaseModel):
    """Arguments for listing GitHub issues."""
    repo: str = Field(default="", description=_REPO_DESC)
    state: str = Field(
        default="open",
        description="Issue state: 'open', 'closed', or 'all'. Default: open.",
    )
    labels: str = Field(
        default="",
        description="Comma-separated label names to filter by (e.g. 'bug,help wanted').",
    )
    assignee: str = Field(
        default="",
        description="Filter by assignee username. Leave empty for all assignees.",
    )
    per_page: int = Field(
        default=20, ge=1, le=100,
        description="Number of issues to return (1-100). Default: 20.",
    )


class GithubListPRsArgs(BaseModel):
    """Arguments for listing GitHub pull requests."""
    repo: str = Field(default="", description=_REPO_DESC)
    state: str = Field(
        default="open",
        description="PR state: 'open', 'closed', or 'all'. Default: open.",
    )
    base: str = Field(
        default="",
        description="Filter by target base branch (e.g. 'main'). Empty = all branches.",
    )
    per_page: int = Field(
        default=20, ge=1, le=100,
        description="Number of PRs to return (1-100). Default: 20.",
    )


class GithubGetPRArgs(BaseModel):
    """Arguments for getting a specific pull request."""
    pr_number: int = Field(description="The PR number to retrieve.", ge=1)
    repo: str = Field(default="", description=_REPO_DESC)


class GithubCreateIssueArgs(BaseModel):
    """Arguments for creating a GitHub issue."""
    title: str = Field(description="Issue title.")
    body: str = Field(default="", description="Issue body in Markdown.")
    labels: str = Field(
        default="",
        description="Comma-separated label names to apply (e.g. 'bug,enhancement').",
    )
    assignee: str = Field(default="", description="Username to assign to this issue.")
    repo: str = Field(default="", description=_REPO_DESC)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()


class GithubCreatePRArgs(BaseModel):
    """Arguments for creating a GitHub pull request."""
    title: str = Field(description="PR title.")
    branch: str = Field(
        description="Head branch to merge FROM (must exist on remote — use git_push first).",
    )
    base: str = Field(default="main", description="Target base branch to merge INTO. Default: 'main'.")
    body: str = Field(default="", description="PR description in Markdown.")
    draft: bool = Field(default=False, description="Create as draft PR. Default: False.")
    repo: str = Field(default="", description=_REPO_DESC)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @field_validator("branch")
    @classmethod
    def branch_not_empty(cls, v):
        if not v.strip():
            raise ValueError("branch cannot be empty")
        return v.strip()


class GithubCommentArgs(BaseModel):
    """Arguments for commenting on a GitHub issue or PR."""
    number: int = Field(description="Issue or PR number to comment on.", ge=1)
    body: str = Field(description="Comment body in Markdown.")
    repo: str = Field(default="", description=_REPO_DESC)

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v):
        if not v.strip():
            raise ValueError("comment body cannot be empty")
        return v.strip()


# ═══════════════════════════════════════════════════════════
# GitLab Integration
# ═══════════════════════════════════════════════════════════

_GL_REPO_DESC = (
    "GitLab project as 'namespace/project' (e.g. 'mygroup/myrepo'). "
    "Leave empty to auto-detect from git remote origin."
)

_GL_STATE_ISSUE = "Issue state: 'opened', 'closed', or 'all'. Default: opened."
_GL_STATE_MR = "MR state: 'opened', 'closed', 'merged', or 'all'. Default: opened."


class GitlabListIssuesArgs(BaseModel):
    """Arguments for listing GitLab issues."""
    repo: str = Field(default="", description=_GL_REPO_DESC)
    state: str = Field(default="opened", description=_GL_STATE_ISSUE)
    labels: str = Field(
        default="",
        description="Comma-separated label names to filter by (e.g. 'bug,help wanted').",
    )
    assignee: str = Field(
        default="",
        description="Filter by assignee username. Leave empty for all assignees.",
    )
    per_page: int = Field(
        default=20, ge=1, le=100,
        description="Number of issues to return (1-100). Default: 20.",
    )


class GitlabListMRsArgs(BaseModel):
    """Arguments for listing GitLab merge requests."""
    repo: str = Field(default="", description=_GL_REPO_DESC)
    state: str = Field(default="opened", description=_GL_STATE_MR)
    target_branch: str = Field(
        default="",
        description="Filter by target branch (e.g. 'main'). Empty = all branches.",
    )
    per_page: int = Field(
        default=20, ge=1, le=100,
        description="Number of MRs to return (1-100). Default: 20.",
    )


class GitlabGetMRArgs(BaseModel):
    """Arguments for getting a specific GitLab merge request."""
    mr_number: int = Field(description="The MR IID (internal ID) to retrieve.", ge=1)
    repo: str = Field(default="", description=_GL_REPO_DESC)


class GitlabCreateIssueArgs(BaseModel):
    """Arguments for creating a GitLab issue."""
    title: str = Field(description="Issue title.")
    body: str = Field(default="", description="Issue description in Markdown.")
    labels: str = Field(
        default="",
        description="Comma-separated label names to apply (e.g. 'bug,enhancement').",
    )
    assignee: str = Field(
        default="",
        description="Username or numeric user ID to assign to this issue.",
    )
    repo: str = Field(default="", description=_GL_REPO_DESC)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()


class GitlabCreateMRArgs(BaseModel):
    """Arguments for creating a GitLab merge request."""
    title: str = Field(description="MR title.")
    source_branch: str = Field(
        description="Source branch to merge FROM (must exist on remote — use git_push first).",
    )
    target_branch: str = Field(
        default="main",
        description="Target branch to merge INTO. Default: 'main'.",
    )
    description: str = Field(default="", description="MR description in Markdown.")
    draft: bool = Field(
        default=False,
        description="Create as draft MR (prefixes title with 'Draft:'). Default: False.",
    )
    remove_source_branch: bool = Field(
        default=False,
        description="Delete source branch after merge. Default: False.",
    )
    repo: str = Field(default="", description=_GL_REPO_DESC)

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @field_validator("source_branch")
    @classmethod
    def branch_not_empty(cls, v):
        if not v.strip():
            raise ValueError("source_branch cannot be empty")
        return v.strip()


class GitlabCommentArgs(BaseModel):
    """Arguments for commenting on a GitLab issue or MR."""
    number: int = Field(description="Issue IID or MR IID to comment on.", ge=1)
    body: str = Field(description="Comment body in Markdown.")
    resource_type: str = Field(
        default="issue",
        description="Target resource: 'issue' (default) or 'mr' (merge request).",
    )
    repo: str = Field(default="", description=_GL_REPO_DESC)

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v):
        if not v.strip():
            raise ValueError("comment body cannot be empty")
        return v.strip()

    @field_validator("resource_type")
    @classmethod
    def valid_resource_type(cls, v):
        if v not in ("issue", "mr"):
            raise ValueError("resource_type must be 'issue' or 'mr'")
        return v


# ── Skill Hub ─────────────────────────────────────────────────

class HubSearchArgs(BaseModel):
    """Arguments for searching the Skill Hub index."""
    query: str = Field(
        default="",
        description="Keyword to search across skill names, descriptions, and tags.",
    )
    category: str = Field(
        default="",
        description="Filter by category (e.g. 'devops', 'testing', 'refactor').",
    )
    tag: str = Field(
        default="",
        description="Filter by a specific tag.",
    )


class SkillInstallArgs(BaseModel):
    """Arguments for installing a skill from the Hub or a direct URL."""
    name: str = Field(
        description=(
            "Skill name to install (looked up in the hub index), "
            "or the local filename stem when providing a direct url."
        )
    )
    url: str = Field(
        default="",
        description=(
            "Optional direct URL to a .md or .py skill file. "
            "When provided, the hub index is not consulted."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description="Replace an already-installed skill with the same name.",
    )

    @field_validator("name")
    @classmethod
    def name_valid(cls, v):
        import re as _re
        if not _re.match(r"^[\w][\w\-]*$", v):
            raise ValueError(
                "Skill name must start with a letter/digit and contain only "
                "letters, digits, and hyphens."
            )
        return v


class SkillRemoveArgs(BaseModel):
    """Arguments for removing an installed skill."""
    name: str = Field(
        description="Name of the skill to remove (markdown or plugin).",
    )
