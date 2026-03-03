"""
Tool: context_build — automatically gather relevant files for a task.

Given a task description, uses multiple signals to find related code:
1. Semantic search (vector similarity, if indexed)
2. Keyword search (ripgrep over workspace)
3. Import/dep analysis (for Python entry points)
4. Deduplication + ranking

Returns a structured context package: file paths with relevance reasons,
ready for the agent to read before starting work.
"""
import os
import re
from langchain_core.tools import tool

import config
from agent.tools.utils import IGNORE_DIRS
from models.tool_schemas import ContextBuildArgs


# ── Helpers ───────────────────────────────────────────────────

def _extract_keywords(description: str) -> list[str]:
    """Extract significant keywords from the task description."""
    # Remove common stop words
    stop = {
        "the", "a", "an", "to", "for", "in", "of", "and", "or", "is", "are",
        "was", "be", "it", "this", "that", "fix", "add", "update", "change",
        "implement", "create", "make", "how", "what", "where", "when", "with",
        "from", "into", "by", "on", "at", "as", "if", "so", "but", "not",
        "all", "any", "get", "set", "use", "do", "run", "work", "new", "my",
    }

    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b', description)
    keywords = [w.lower() for w in words if w.lower() not in stop]

    # Prioritize multi-word technical terms (CamelCase, snake_case)
    technical = [w for w in words if '_' in w or (w[0].isupper() and len(w) > 3)]

    # Combine, deduplicate, prioritize technical
    seen = set()
    result = []
    for w in technical + keywords:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            result.append(w)

    return result[:12]  # top 12 keywords


def _keyword_search(keywords: list[str], workspace: str, max_per_kw: int = 5) -> dict[str, list[str]]:
    """Search for files matching keywords using ripgrep or Python fallback.

    Returns: {file_path: [matched_keyword, ...]}
    """
    import subprocess
    import shutil

    file_matches: dict[str, list[str]] = {}

    rg_path = shutil.which(getattr(config, "RIPGREP_PATH", "rg"))

    for keyword in keywords[:8]:  # limit to top 8 keywords
        if rg_path:
            try:
                result = subprocess.run(
                    [rg_path, "--files-with-matches", "--ignore-case",
                     "--max-count=1", keyword, workspace],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace",
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines()[:max_per_kw]:
                        fpath = line.strip()
                        if os.path.isfile(fpath):
                            file_matches.setdefault(fpath, []).append(keyword)
                    continue
            except Exception:
                pass

        # Python fallback
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                if not fname.endswith(('.py', '.js', '.ts', '.go', '.rs', '.java', '.c', '.cpp')):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read(20000)
                    if keyword.lower() in content.lower():
                        file_matches.setdefault(fpath, []).append(keyword)
                except Exception:
                    pass

    return file_matches


def _semantic_search_files(description: str, n: int = 8) -> dict[str, float]:
    """Try semantic search, return {file_path: score}. Silently skip if not indexed."""
    try:
        from indexer import get_indexer
        indexer = get_indexer()
        if indexer.total_chunks == 0:
            return {}

        results = indexer.search(description, n_results=n)
        file_scores: dict[str, float] = {}
        for r in results:
            if "error" in r:
                return {}
            fp = r.get("file", "")
            score = r.get("score", 0)
            if fp:
                abs_fp = os.path.join(indexer.workspace, fp)
                file_scores[abs_fp] = max(file_scores.get(abs_fp, 0), score)
        return file_scores
    except Exception:
        return {}


def _follow_python_imports(entry_files: list[str], workspace: str, max_depth: int = 1) -> list[str]:
    """Follow one level of Python imports from entry files."""
    import ast

    additional = []
    for fpath in entry_files:
        if not fpath.endswith('.py'):
            continue
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                source = f.read(50000)
            tree = ast.parse(source)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split('.')
                for root in [workspace, os.path.dirname(fpath)]:
                    candidate = os.path.join(root, *parts) + '.py'
                    if os.path.isfile(candidate) and candidate not in additional:
                        additional.append(candidate)
                        break
                    candidate = os.path.join(root, *parts, '__init__.py')
                    if os.path.isfile(candidate) and candidate not in additional:
                        additional.append(candidate)
                        break

    return additional


# ── Tool ──────────────────────────────────────────────────────

@tool(args_schema=ContextBuildArgs)
def context_build(description: str, max_files: int = 10, include_deps: bool = True) -> str:
    """Automatically find the most relevant files for a task.

    Uses three complementary signals to gather context:
    1. **Semantic search** — vector similarity (if codebase is indexed)
    2. **Keyword search** — ripgrep for technical terms extracted from the description
    3. **Import analysis** — follows Python imports from matched files (if include_deps=True)

    Returns a ranked list of files with relevance reasons.
    Use this at the start of a complex task to quickly identify which files to read.

    Args:
        description: Natural language description of your task or question.
        max_files: Maximum files to include (1-30). Default 10.
        include_deps: Follow Python import dependencies. Default True.

    Returns:
        Ranked file list with relevance signals. Read these files before planning.
    """
    workspace = config.WORKSPACE_DIR

    # Step 1: Semantic search
    semantic_scores = _semantic_search_files(description, n=max_files)

    # Step 2: Keyword search
    keywords = _extract_keywords(description)
    kw_matches = _keyword_search(keywords, workspace)

    # Step 3: Combine scores
    # file_path → {semantic: float, kw_hits: int, reasons: list[str]}
    file_info: dict[str, dict] = {}

    for fpath, score in semantic_scores.items():
        if os.path.isfile(fpath):
            file_info[fpath] = {"semantic": score, "kw_hits": 0, "reasons": [f"semantic match ({score:.0%})"]}

    for fpath, matched_kws in kw_matches.items():
        if fpath in file_info:
            file_info[fpath]["kw_hits"] = len(matched_kws)
            file_info[fpath]["reasons"].extend([f"keyword: '{k}'" for k in matched_kws[:3]])
        else:
            file_info[fpath] = {
                "semantic": 0,
                "kw_hits": len(matched_kws),
                "reasons": [f"keyword: '{k}'" for k in matched_kws[:3]],
            }

    # Step 4: Rank by combined score
    def rank_score(info: dict) -> float:
        return info["semantic"] * 2 + info["kw_hits"] * 0.5

    ranked = sorted(file_info.keys(), key=lambda f: rank_score(file_info[f]), reverse=True)
    top_files = ranked[:max_files]

    # Step 5: Follow imports for Python files
    extra_imports = []
    if include_deps and top_files:
        extra_imports = _follow_python_imports(top_files[:5], workspace)
        # Add imports that aren't already in top_files
        for imp in extra_imports:
            if imp not in top_files and len(top_files) < max_files:
                top_files.append(imp)
                file_info[imp] = {
                    "semantic": 0, "kw_hits": 0,
                    "reasons": ["imported by matched file"],
                }

    if not top_files:
        return (
            f"No relevant files found for: '{description}'\n\n"
            "Suggestions:\n"
            "  - Use index_codebase to enable semantic search\n"
            "  - Try more specific technical terms\n"
            "  - Use file_list to explore the project structure manually"
        )

    # Format output
    lines = [
        f"🎯 Context for: '{description}'",
        f"   Found {len(top_files)} relevant files (keywords: {', '.join(keywords[:5])})",
        "",
        "## Recommended files to read:",
        "",
    ]

    for i, fpath in enumerate(top_files, 1):
        rel = os.path.relpath(fpath, workspace).replace("\\", "/")
        info = file_info[fpath]
        reasons = " | ".join(info["reasons"][:3])

        try:
            size = os.path.getsize(fpath)
            lines_count = 0
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                lines_count = sum(1 for _ in f)
            size_str = f"{lines_count} lines"
        except Exception:
            size_str = "?"

        lines.append(f"  {i:2d}. {rel}  ({size_str})")
        lines.append(f"      Why: {reasons}")

    lines.append("")
    lines.append("## Suggested next step:")
    file_args = [os.path.relpath(f, workspace).replace("\\", "/") for f in top_files[:5]]
    lines.append(f"  Use batch_read([{', '.join(repr(f) for f in file_args)}]) to read the top files at once.")

    if not semantic_scores and not extra_imports:
        lines.append("")
        lines.append("💡 Tip: Run index_codebase() first to enable semantic search for better results.")

    return "\n".join(lines)
