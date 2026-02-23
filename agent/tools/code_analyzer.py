"""
Tool: code_analyzer — file outline, function list, metrics.
All outputs go through universal truncation.
"""
import os
import re
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from models.tool_schemas import CodeAnalyzeArgs


@tool(args_schema=CodeAnalyzeArgs)
def code_analyze(file_path: str) -> str:
    """Analyze a code file and return its structure outline.

    Extracts functions, classes, imports, and basic metrics.
    Supports: Python, JavaScript/TypeScript, C/C++, Java, Matlab.

    Args:
        file_path: Path to the file to analyze.

    Returns:
        Structured outline with functions, classes, and metrics.
    """
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        return f"Error: File '{file_path}' not found."

    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            lines = content.split("\n")
    except PermissionError:
        return f"Error: Permission denied reading '{file_path}'."

    ext = os.path.splitext(resolved)[1].lower()
    total_lines = len(lines)
    blank_lines = sum(1 for l in lines if not l.strip())
    comment_lines = _count_comments(lines, ext)

    output = [
        f"📊 Analysis: {os.path.basename(resolved)}",
        f"   Path: {resolved}",
        f"   Lines: {total_lines} total | {total_lines - blank_lines - comment_lines} code | {comment_lines} comments | {blank_lines} blank",
        "",
    ]

    # Extract structure based on language
    functions = _extract_functions(content, ext)
    classes = _extract_classes(content, ext)
    imports = _extract_imports(content, ext)

    if imports:
        output.append(f"📦 Imports ({len(imports)}):")
        for imp in imports[:20]:
            output.append(f"   {imp}")
        if len(imports) > 20:
            output.append(f"   ... and {len(imports) - 20} more")
        output.append("")

    if classes:
        output.append(f"🏗️  Classes ({len(classes)}):")
        for name, line_num in classes:
            output.append(f"   L{line_num}: {name}")
        output.append("")

    if functions:
        output.append(f"⚡ Functions ({len(functions)}):")
        for name, line_num in functions:
            output.append(f"   L{line_num}: {name}")
        output.append("")

    return truncate_output("\n".join(output))


def _extract_functions(content: str, ext: str) -> list[tuple[str, int]]:
    """Extract function definitions with line numbers."""
    patterns = {
        ".py": r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
        ".js": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ".ts": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ".jsx": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ".tsx": r"(?:^|\s)(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ".c": r"^\w[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*\{",
        ".cpp": r"^\w[\w\s\*:]+\s+(\w+)\s*\([^)]*\)\s*\{",
        ".h": r"^\w[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*;",
        ".java": r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(",
        ".m": r"^function\s+(?:\[?\w+(?:,\s*\w+)*\]?\s*=\s*)?(\w+)\s*\(",
    }

    pat = patterns.get(ext)
    if not pat:
        return []

    results = []
    for i, line in enumerate(content.split("\n"), 1):
        m = re.match(pat, line)
        if m:
            name = next((g for g in m.groups() if g), None)
            if name:
                results.append((name, i))
    return results


def _extract_classes(content: str, ext: str) -> list[tuple[str, int]]:
    """Extract class definitions."""
    patterns = {
        ".py": r"^\s*class\s+(\w+)",
        ".js": r"class\s+(\w+)",
        ".ts": r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)",
        ".java": r"(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)",
        ".cpp": r"class\s+(\w+)",
    }

    pat = patterns.get(ext)
    if not pat:
        return []

    results = []
    for i, line in enumerate(content.split("\n"), 1):
        m = re.match(pat, line)
        if m:
            results.append((m.group(1), i))
    return results


def _extract_imports(content: str, ext: str) -> list[str]:
    """Extract import statements."""
    patterns = {
        ".py": r"^(?:from\s+\S+\s+)?import\s+.+",
        ".js": r"^import\s+.+",
        ".ts": r"^import\s+.+",
        ".c": r"^#include\s+.+",
        ".cpp": r"^#include\s+.+",
        ".java": r"^import\s+.+",
    }

    pat = patterns.get(ext)
    if not pat:
        return []

    return [line.strip() for line in content.split("\n") if re.match(pat, line.strip())]


def _count_comments(lines: list[str], ext: str) -> int:
    """Count comment lines."""
    count = 0
    in_block = False

    for line in lines:
        stripped = line.strip()
        if ext in (".py",):
            if stripped.startswith("#"):
                count += 1
            elif '"""' in stripped or "'''" in stripped:
                count += 1
                in_block = not in_block
            elif in_block:
                count += 1
        elif ext in (".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h"):
            if in_block:
                count += 1
                if "*/" in stripped:
                    in_block = False
            elif stripped.startswith("//"):
                count += 1
            elif stripped.startswith("/*"):
                count += 1
                if "*/" not in stripped:
                    in_block = True
        elif ext == ".m":
            if stripped.startswith("%"):
                count += 1

    return count


def _resolve_path(p: str) -> str:
    if os.path.isabs(p):
        return p
    return os.path.join(config.WORKSPACE_DIR, p)
