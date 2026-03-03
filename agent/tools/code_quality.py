"""
Tool: code_quality — static analysis for source files.

Python: full AST-based analysis (cyclomatic complexity, nesting, params).
JS/TS/Go/Rust/Java/C/C++: regex-based structural analysis.

Reports:
- Functions/methods with high complexity
- Long functions
- Functions with too many parameters (Python only)
- Deeply nested code (Python only)
- TODO/FIXME/HACK/XXX comment locations
- Overall file metrics (LOC, functions, classes)
"""
import ast
import os
import re
from langchain_core.tools import tool

from agent.tools.utils import resolve_tool_path
from models.tool_schemas import CodeQualityArgs


# ── Python: cyclomatic complexity ─────────────────────────────

class _ComplexityVisitor(ast.NodeVisitor):
    """Count branches that increase cyclomatic complexity."""

    def __init__(self):
        self.complexity = 1  # base

    def visit_If(self, node):
        self.complexity += 1
        if node.orelse:
            self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node):
        self.complexity += len(node.items)
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)


def _cyclomatic_complexity(func_node: ast.AST) -> int:
    visitor = _ComplexityVisitor()
    visitor.visit(func_node)
    return visitor.complexity


def _max_nesting(node: ast.AST) -> int:
    """Compute maximum nesting depth inside a function."""
    NESTING_NODES = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.ExceptHandler)

    def _depth(n, current=0):
        max_d = current
        for child in ast.iter_child_nodes(n):
            if isinstance(child, NESTING_NODES):
                d = _depth(child, current + 1)
            else:
                d = _depth(child, current)
            if d > max_d:
                max_d = d
        return max_d

    return _depth(node)


# ── Generic multi-language analysis ───────────────────────────

# Language configs: extension → (func_patterns, complexity_kws, todo_re, line_comment)
_LANG_CONFIGS = {
    # JavaScript / TypeScript
    ".js":  "js",
    ".jsx": "js",
    ".ts":  "ts",
    ".tsx": "ts",
    ".mjs": "js",
    ".cjs": "js",
    # Go
    ".go":  "go",
    # Rust
    ".rs":  "rust",
    # Java
    ".java": "java",
    # C / C++
    ".c":   "c",
    ".cpp": "cpp",
    ".cc":  "cpp",
    ".cxx": "cpp",
    ".h":   "c",
    ".hpp": "cpp",
}

# Per-language: (func_regex_list, class_regex, complexity_keywords, todo_comment_re)
_LANG_RULES = {
    "js": {
        "func": [
            r'(?:^|\s)function\s+(\w+)\s*\(',           # function foo(
            r'(?:^|\s)(?:async\s+)?function\*?\s+(\w+)\s*\(',  # async function* foo(
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(',  # const foo = (
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\w+|\([^)]*\))\s*=>',  # const foo = x =>
        ],
        "class": [r'class\s+(\w+)'],
        "complexity": ["if", "else if", "for", "while", "switch", "catch", " && ", " || ", "case "],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "ts": {
        "func": [
            r'(?:^|\s)(?:async\s+)?function\*?\s+(\w+)\s*[<(]',
            r'(?:const|let|var)\s+(\w+)\s*[=:][^=].*(?:async\s*)?\(',
            r'(?:public|private|protected|static|abstract|override)(?:\s+\w+)*\s+(\w+)\s*[<(]',
        ],
        "class": [r'class\s+(\w+)', r'interface\s+(\w+)', r'type\s+(\w+)\s*='],
        "complexity": ["if", "else if", "for", "while", "switch", "catch", " && ", " || ", "case "],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "go": {
        "func": [
            r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(',  # func (recv Type) Name(
        ],
        "class": [r'type\s+(\w+)\s+struct', r'type\s+(\w+)\s+interface'],
        "complexity": ["if ", "else if ", "for ", "switch ", "case ", " && ", " || ", "select {"],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "rust": {
        "func": [
            r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(',
        ],
        "class": [r'struct\s+(\w+)', r'enum\s+(\w+)', r'trait\s+(\w+)', r'impl\s+(?:\w+\s+for\s+)?(\w+)'],
        "complexity": ["if ", "else if ", "for ", "while ", "match ", " && ", " || ", "loop {"],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "java": {
        "func": [
            r'(?:public|private|protected|static|final|abstract|synchronized)(?:\s+\w+)*\s+(\w+)\s*\(',
        ],
        "class": [r'class\s+(\w+)', r'interface\s+(\w+)', r'enum\s+(\w+)', r'record\s+(\w+)'],
        "complexity": ["if ", "else if ", "for ", "while ", "switch ", "catch ", " && ", " || ", "case "],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "c": {
        "func": [
            r'^\w[\w\s\*]+\s+(\w+)\s*\([^;]*\)\s*\{',
        ],
        "class": [r'struct\s+(\w+)', r'enum\s+(\w+)', r'typedef\s+struct\s+\w*\s*\{'],
        "complexity": ["if ", "else if ", "for ", "while ", "switch ", " && ", " || ", "case "],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
    "cpp": {
        "func": [
            r'(?:virtual\s+|static\s+|inline\s+|explicit\s+)?[\w:<>\*&]+\s+(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{',
        ],
        "class": [r'class\s+(\w+)', r'struct\s+(\w+)', r'enum\s+(?:class\s+)?(\w+)'],
        "complexity": ["if ", "else if ", "for ", "while ", "switch ", "catch ", " && ", " || ", "case "],
        "todo": r'//\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b',
        "line_comment": "//",
    },
}


def _analyze_generic(source: str, file_path: str, lang_key: str, include_todos: bool) -> str:
    """Regex-based analysis for non-Python languages."""
    rules = _LANG_RULES[lang_key]
    lines = source.splitlines()
    total_lines = len(lines)

    # Count blank / comment lines
    line_comment = rules["line_comment"]
    blank_lines = sum(1 for l in lines if not l.strip())
    comment_lines = sum(1 for l in lines if l.strip().startswith(line_comment))

    # Find functions (name + line number)
    funcs = []
    seen_func_names: set = set()
    for i, line in enumerate(lines, 1):
        for pat in rules["func"]:
            m = re.search(pat, line)
            if m:
                name = m.group(1)
                if name not in seen_func_names:
                    seen_func_names.add(name)
                    funcs.append({"name": name, "line": i})
                break

    # Find classes/structs
    classes = []
    seen_class_names: set = set()
    for i, line in enumerate(lines, 1):
        for pat in rules["class"]:
            m = re.search(pat, line)
            if m:
                name = m.group(1)
                if name not in seen_class_names:
                    seen_class_names.add(name)
                    classes.append(name)
                break

    # Count complexity keywords (rough estimate)
    complexity_count = 0
    for kw in rules["complexity"]:
        complexity_count += source.count(kw)

    # TODOs
    todos = []
    if include_todos:
        todo_re = re.compile(rules["todo"], re.IGNORECASE)
        for i, line in enumerate(lines, 1):
            m = todo_re.search(line)
            if m:
                todos.append((i, line.strip()[:120]))

    # ── Build report ──────────────────────────────────────
    parts = []
    lang_display = lang_key.upper()
    parts.append(f"📊 Code Quality Report: {file_path}  [{lang_display}]")
    parts.append("─" * 60)

    parts.append("## File Statistics")
    parts.append(f"  Total lines    : {total_lines}")
    parts.append(f"  Blank lines    : {blank_lines}")
    parts.append(f"  Comment lines  : {comment_lines}")
    parts.append(f"  Code lines     : {total_lines - blank_lines - comment_lines}")
    parts.append(f"  Functions      : {len(funcs)}")
    parts.append(f"  Classes/Types  : {len(classes)}")
    if classes:
        parts.append(f"  Names          : {', '.join(classes[:8])}")
    parts.append(f"  Complexity est.: {complexity_count} branch keywords")
    parts.append("")

    # Simple score: deduct for high complexity, lots of TODOs
    score = max(0, 100 - max(0, complexity_count - 20) - len(todos))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    parts.append(f"## Quality Score: {score}/100 (Grade {grade})")
    parts.append("  *(Based on branch keyword count — use a language-specific linter for precise metrics)*")
    parts.append("")

    if complexity_count > 50:
        parts.append(f"### ⚠️ High Complexity Signal")
        parts.append(f"  Found {complexity_count} branch keywords (if/for/while/&&/||/switch).")
        parts.append("  Consider breaking large functions into smaller units.")
        parts.append("")

    if todos:
        parts.append(f"### 📝 TODOs/FIXMEs ({len(todos)} found)")
        for lineno, text in todos[:20]:
            parts.append(f"  L{lineno:4d}: {text}")
        if len(todos) > 20:
            parts.append(f"  ... and {len(todos) - 20} more")
        parts.append("")

    # Function list (first 20)
    if funcs:
        parts.append(f"### Function/Method Index (first 20 of {len(funcs)})")
        for f in funcs[:20]:
            parts.append(f"  L{f['line']:4d}  {f['name']}()")
        if len(funcs) > 20:
            parts.append(f"  ... and {len(funcs) - 20} more")

    if not todos and complexity_count <= 20:
        parts.append("✅ No obvious issues found.")

    return "\n".join(parts)


# ── Python AST analysis ─────────────────────────────────────

def _analyze_python(source: str, file_path: str, resolved: str, include_todos: bool) -> str:
    """Full AST-based analysis for Python files."""
    try:
        tree = ast.parse(source, filename=resolved)
    except SyntaxError as e:
        return f"❌ Syntax error in {file_path}: {e}"

    lines = source.splitlines()
    total_lines = len(lines)

    functions = []
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", node.lineno)
            func_len = end_line - node.lineno + 1
            n_params = (len(node.args.args) + len(node.args.posonlyargs)
                        + len(node.args.kwonlyargs))
            complexity = _cyclomatic_complexity(node)
            nesting = _max_nesting(node)
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "end_line": end_line,
                "length": func_len,
                "params": n_params,
                "complexity": complexity,
                "nesting": nesting,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)

    todo_pattern = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|NOTE|BUG)\b.*", re.IGNORECASE)
    todos = []
    if include_todos:
        for i, line in enumerate(lines, 1):
            m = todo_pattern.search(line)
            if m:
                todos.append((i, m.group(0).strip()))

    blank_lines = sum(1 for l in lines if not l.strip())
    comment_lines = sum(1 for l in lines if l.strip().startswith("#"))

    report_parts = []
    report_parts.append(f"📊 Code Quality Report: {file_path}  [Python]")
    report_parts.append("─" * 60)
    report_parts.append("## File Statistics")
    report_parts.append(f"  Total lines    : {total_lines}")
    report_parts.append(f"  Blank lines    : {blank_lines}")
    report_parts.append(f"  Comment lines  : {comment_lines}")
    report_parts.append(f"  Code lines     : {total_lines - blank_lines - comment_lines}")
    report_parts.append(f"  Functions      : {len(functions)}")
    report_parts.append(f"  Classes        : {len(classes)}")
    if classes:
        report_parts.append(f"  Class names    : {', '.join(classes)}")
    report_parts.append("")

    complex_fns = [f for f in functions if f["complexity"] > 10]
    long_fns = [f for f in functions if f["length"] > 50]
    param_fns = [f for f in functions if f["params"] > 7]
    nested_fns = [f for f in functions if f["nesting"] > 4]

    total_issues = len(complex_fns) + len(long_fns) + len(param_fns) + len(nested_fns)
    score = max(0, 100 - total_issues * 5 - len(todos) * 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    report_parts.append(f"## Quality Score: {score}/100 (Grade {grade})")
    report_parts.append("")

    if complex_fns:
        report_parts.append(f"### ⚠️ High Complexity ({len(complex_fns)} functions)")
        for f in sorted(complex_fns, key=lambda x: -x["complexity"]):
            level = "🔴 CRITICAL" if f["complexity"] > 20 else "🟡 HIGH"
            report_parts.append(
                f"  {level} {f['name']}() L{f['line']}  "
                f"complexity={f['complexity']} (target: ≤10)"
            )
        report_parts.append("")

    if long_fns:
        report_parts.append(f"### 📏 Long Functions ({len(long_fns)} functions)")
        for f in sorted(long_fns, key=lambda x: -x["length"]):
            report_parts.append(
                f"  L{f['line']}-{f['end_line']}  {f['name']}()  "
                f"{f['length']} lines (target: ≤50)"
            )
        report_parts.append("")

    if param_fns:
        report_parts.append(f"### 🎛️ Too Many Parameters ({len(param_fns)} functions)")
        for f in sorted(param_fns, key=lambda x: -x["params"]):
            report_parts.append(
                f"  L{f['line']}  {f['name']}()  {f['params']} params (target: ≤7)"
            )
        report_parts.append("")

    if nested_fns:
        report_parts.append(f"### 🪆 Deep Nesting ({len(nested_fns)} functions)")
        for f in sorted(nested_fns, key=lambda x: -x["nesting"]):
            report_parts.append(
                f"  L{f['line']}  {f['name']}()  depth={f['nesting']} (target: ≤4)"
            )
        report_parts.append("")

    if todos:
        report_parts.append(f"### 📝 TODOs/FIXMEs ({len(todos)} found)")
        for lineno, text in todos[:20]:
            report_parts.append(f"  L{lineno:4d}: {text}")
        if len(todos) > 20:
            report_parts.append(f"  ... and {len(todos) - 20} more")
        report_parts.append("")

    if total_issues == 0 and not todos:
        report_parts.append("✅ No issues found — this file looks clean!")

    if functions:
        report_parts.append("### Function Complexity Table (top 10 by complexity)")
        report_parts.append("  {:<30} {:>5} {:>8} {:>6} {:>7}".format(
            "Function", "Line", "Cmplxty", "Length", "Params"
        ))
        report_parts.append("  " + "-" * 60)
        for f in sorted(functions, key=lambda x: -x["complexity"])[:10]:
            prefix = "async " if f["is_async"] else ""
            name = f"{prefix}{f['name']}()"
            report_parts.append("  {:<30} {:>5} {:>8} {:>6} {:>7}".format(
                name[:30], f["line"], f["complexity"], f["length"], f["params"]
            ))

    return "\n".join(report_parts)


# ── Tool ──────────────────────────────────────────────────────

@tool(args_schema=CodeQualityArgs)
def code_quality(file_path: str, include_todos: bool = True) -> str:
    """Analyze code quality metrics for a source file.

    **Full AST analysis** for Python:
    - Cyclomatic complexity per function (>10 = warning, >20 = critical)
    - Long functions (>50 lines)
    - Functions with too many parameters (>7)
    - Deeply nested code blocks (>4 levels)
    - Overall quality score (0-100, grade A-D)

    **Structural analysis** for JS/TS/Go/Rust/Java/C/C++:
    - Function/method index with line numbers
    - Class/type/struct/interface names
    - Complexity keyword count (if/for/while/&&/||/switch)
    - TODO/FIXME/HACK locations
    - Line count and code density

    Args:
        file_path: Path to the source file (relative or absolute).
        include_todos: Include TODO/FIXME/HACK/XXX comment locations.

    Returns:
        Quality report with metrics, warnings, and suggestions.
    """
    resolved = resolve_tool_path(file_path)
    if not os.path.isfile(resolved):
        return f"❌ File not found: '{file_path}'"

    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except Exception as e:
        return f"❌ Cannot read file: {e}"

    _, ext = os.path.splitext(resolved)
    ext_lower = ext.lower()

    # Python: full AST analysis
    if ext_lower in (".py", ".pyw"):
        return _analyze_python(source, file_path, resolved, include_todos)

    # Other supported languages: regex-based structural analysis
    lang_key = _LANG_CONFIGS.get(ext_lower)
    if lang_key:
        return _analyze_generic(source, file_path, lang_key, include_todos)

    # Unknown extension: basic stats only
    lines = source.splitlines()
    return (
        f"📊 {file_path}\n"
        f"  Lines: {len(lines)}\n"
        f"  Chars: {len(source)}\n"
        f"  Extension '{ext}' is not supported for deep analysis.\n"
        f"  Supported: .py .js .jsx .ts .tsx .mjs .go .rs .java .c .cpp .h .hpp"
    )
