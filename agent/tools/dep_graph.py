"""
Tool: dep_graph — analyze Python import dependencies.

Traces imports from an entry-point file up to max_depth levels,
building a visual dependency tree. Identifies:
- Direct and transitive imports
- Circular dependencies
- Third-party vs stdlib vs local modules
"""
import ast
import os
import sys
from langchain_core.tools import tool

import config
from agent.tools.utils import resolve_tool_path
from models.tool_schemas import DepGraphArgs


# ── stdlib module set (Python 3.9+) ───────────────────────────

def _get_stdlib_modules() -> set[str]:
    """Return set of stdlib top-level module names."""
    try:
        return sys.stdlib_module_names  # Python 3.10+
    except AttributeError:
        # Fallback: common stdlib modules (Python 3.9+, deduped)
        return {
            "os", "sys", "re", "io", "abc", "ast", "cgi", "cmd", "csv",
            "dis", "enum", "ftplib", "gc", "gzip", "html", "http",
            "json", "logging", "math", "mmap", "pathlib", "pickle",
            "pprint", "queue", "random", "shlex", "shutil", "signal",
            "socket", "sqlite3", "ssl", "stat", "string", "struct",
            "subprocess", "tempfile", "textwrap", "threading",
            "time", "timeit", "tkinter", "token", "tokenize", "traceback",
            "types", "typing", "unicodedata", "unittest", "urllib", "uuid",
            "warnings", "weakref", "xml", "xmlrpc", "zipfile", "zipimport",
            "zlib", "builtins", "collections", "contextlib", "copy",
            "dataclasses", "datetime", "decimal", "difflib", "email",
            "encodings", "fnmatch", "fractions", "functools", "getopt",
            "getpass", "glob", "hashlib", "heapq", "hmac",
            "importlib", "inspect", "itertools", "keyword", "linecache",
            "locale", "multiprocessing", "numbers", "operator", "optparse",
            "platform", "posixpath", "profile", "pdb",
            "concurrent", "asyncio",
        }


_STDLIB = _get_stdlib_modules()


# ── Import extractor ──────────────────────────────────────────

def _extract_imports(source: str) -> list[tuple[str, str]]:
    """Extract all imports from Python source.

    Returns list of (module_name, import_type) where import_type is
    'import', 'from', or 'relative'.
    """
    imports = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name.split(".")[0], "import"))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import
                imports.append(("." * node.level + (node.module or ""), "relative"))
            elif node.module:
                imports.append((node.module.split(".")[0], "from"))

    return imports


def _classify_module(name: str, workspace: str) -> str:
    """Classify a module as 'stdlib', 'local', or 'third_party'."""
    if name.startswith("."):
        return "local"

    base = name.split(".")[0]

    if base in _STDLIB:
        return "stdlib"

    # Check if it exists as a local module/package
    for candidate in [
        os.path.join(workspace, f"{base}.py"),
        os.path.join(workspace, base, "__init__.py"),
    ]:
        if os.path.exists(candidate):
            return "local"

    return "third_party"


def _resolve_local_module(name: str, current_file: str, workspace: str) -> str | None:
    """Resolve a module name to an absolute file path within the workspace."""
    if name.startswith("."):
        # Relative import — resolve relative to current file's directory
        current_dir = os.path.dirname(current_file)
        dots = len(name) - len(name.lstrip("."))
        module_part = name.lstrip(".")

        base_dir = current_dir
        ws_real = os.path.realpath(workspace)
        for _ in range(dots - 1):
            parent = os.path.dirname(base_dir)
            # Prevent escaping workspace boundary
            if not os.path.realpath(parent).startswith(ws_real):
                return None
            base_dir = parent

        if module_part:
            parts = module_part.split(".")
            candidate = os.path.join(base_dir, *parts) + ".py"
            if os.path.isfile(candidate):
                return candidate
            candidate = os.path.join(base_dir, *parts, "__init__.py")
            if os.path.isfile(candidate):
                return candidate
        return None

    # Absolute module name
    parts = name.split(".")
    for root in [workspace, os.path.dirname(current_file)]:
        candidate = os.path.join(root, *parts) + ".py"
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(root, *parts, "__init__.py")
        if os.path.isfile(candidate):
            return candidate

    return None


# ── Graph builder ─────────────────────────────────────────────

def _build_graph(
    entry: str,
    workspace: str,
    max_depth: int,
    show_stdlib: bool,
) -> dict:
    """
    Build dependency graph via BFS.

    Returns:
        {
            "nodes": {file_path: {imports: [...], type: 'entry'|'local'|...}},
            "edges": [(from_path, to_path, module_name)],
            "unresolved": {module_name: classification},
            "circular": [(path_a, path_b)],
        }
    """
    nodes = {}       # path → {imports, depth}
    edges = []       # (from, to, name)
    unresolved = {}  # name → classification
    visited = set()
    circular = []
    # Track the ancestor chain per BFS path for indirect cycle detection
    ancestors: dict[str, set[str]] = {}  # file → set of all ancestors in its path

    queue = [(entry, 0, set())]  # (file, depth, ancestor_set)

    while queue:
        current_file, depth, current_ancestors = queue.pop(0)

        if current_file in visited:
            continue
        visited.add(current_file)
        ancestors[current_file] = current_ancestors

        # Read file
        try:
            with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except Exception:
            continue

        raw_imports = _extract_imports(source)
        node_imports = []

        for mod_name, import_type in raw_imports:
            classification = _classify_module(mod_name, workspace)

            if classification == "stdlib" and not show_stdlib:
                continue

            if classification == "local":
                resolved = _resolve_local_module(mod_name, current_file, workspace)
                if resolved:
                    # Cycle detection: check both visited set AND ancestor chain
                    if resolved in current_ancestors:
                        # Indirect cycle: A→B→...→current→resolved where resolved is an ancestor
                        circular.append((current_file, resolved))
                    elif resolved in visited and depth < max_depth:
                        circular.append((current_file, resolved))
                    elif depth < max_depth:
                        queue.append((resolved, depth + 1, current_ancestors | {current_file}))
                    edges.append((current_file, resolved, mod_name))
                    node_imports.append({"name": mod_name, "type": classification, "resolved": resolved})
                else:
                    node_imports.append({"name": mod_name, "type": "local_unresolved"})
            else:
                node_imports.append({"name": mod_name, "type": classification})
                if classification != "stdlib":
                    unresolved[mod_name] = classification

        nodes[current_file] = {"imports": node_imports, "depth": depth}

    return {
        "nodes": nodes,
        "edges": edges,
        "unresolved": unresolved,
        "circular": circular,
    }


def _format_graph(
    graph: dict,
    entry: str,
    workspace: str,
    max_depth: int,
) -> str:
    """Format the dependency graph as a readable tree."""
    nodes = graph["nodes"]
    edges = graph["edges"]
    circular = graph["circular"]
    unresolved = graph["unresolved"]

    def rel(p):
        try:
            return os.path.relpath(p, workspace)
        except ValueError:
            return p

    lines = [f"🔗 Dependency Graph: {rel(entry)} (depth={max_depth})"]
    lines.append("─" * 60)

    # Build adjacency list
    children: dict[str, list[tuple[str, str]]] = {}
    for src, dst, name in edges:
        children.setdefault(src, []).append((dst, name))

    # Tree render
    def render(path, prefix="", visited_tree=None):
        if visited_tree is None:
            visited_tree = set()
        visited_tree.add(path)

        node = nodes.get(path, {})
        imports = node.get("imports", [])
        kids = children.get(path, [])

        # Show non-local imports inline
        non_local = [i for i in imports if i["type"] in ("third_party",)]
        if non_local:
            names = ", ".join(i["name"] for i in non_local[:6])
            if len(non_local) > 6:
                names += f" +{len(non_local)-6} more"
            lines.append(f"{prefix}  [3rd party: {names}]")

        for i, (child, name) in enumerate(kids):
            is_last = i == len(kids) - 1
            conn = "└── " if is_last else "├── "
            ext = "    " if is_last else "│   "
            child_rel = rel(child)

            if child in visited_tree:
                lines.append(f"{prefix}{conn}🔄 {child_rel} (circular)")
            else:
                lines.append(f"{prefix}{conn}📄 {child_rel}")
                render(child, prefix + ext, visited_tree | {path})

    lines.append(f"📄 {rel(entry)}")
    render(entry)

    lines.append("")

    # Summary stats
    lines.append("## Summary")
    lines.append(f"  Local files analyzed : {len(nodes)}")
    lines.append(f"  Import edges         : {len(edges)}")

    third_party = sorted(set(
        i["name"] for node in nodes.values()
        for i in node["imports"]
        if i["type"] == "third_party"
    ))
    if third_party:
        lines.append(f"  Third-party packages : {', '.join(third_party[:10])}")
        if len(third_party) > 10:
            lines.append(f"                         ... and {len(third_party) - 10} more")

    if circular:
        lines.append(f"\n  ⚠️ Circular imports ({len(circular)}):")
        for a, b in circular:
            lines.append(f"    {rel(a)} ↔ {rel(b)}")

    return "\n".join(lines)


# ── Tool ──────────────────────────────────────────────────────

@tool(args_schema=DepGraphArgs)
def dep_graph(file_path: str, max_depth: int = 2, show_stdlib: bool = False) -> str:
    """Analyze Python import dependencies starting from an entry-point file.

    Builds a tree showing which local files import each other, plus
    third-party packages used. Detects circular imports.

    Args:
        file_path: Entry-point Python file to start analysis from.
        max_depth: How many import levels to follow (1-5). Default 2.
        show_stdlib: Include stdlib imports (os, sys, etc.) in output.

    Returns:
        Formatted dependency tree with summary statistics.
    """
    resolved = resolve_tool_path(file_path)
    if not os.path.isfile(resolved):
        return f"❌ File not found: '{file_path}'"

    _, ext = os.path.splitext(resolved)
    if ext.lower() not in (".py", ".pyw"):
        return f"❌ dep_graph only supports Python files. Got: '{ext}'"

    workspace = config.WORKSPACE_DIR
    graph = _build_graph(resolved, workspace, max_depth, show_stdlib)
    return _format_graph(graph, resolved, workspace, max_depth)
