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
