import logging
import os
import re
from typing import List, Dict
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

# Helper to find definitions using AST/Regex heuristics
def _find_definition_regex(workspace: str, symbol: str) -> List[Dict]:
    results = []
    # Patterns for Python, JS, TS, etc.
    patterns = [
        re.compile(fr"^(?:async\s+)?def\s+({symbol})\b", re.MULTILINE),
        re.compile(fr"^class\s+({symbol})\b", re.MULTILINE),
        re.compile(fr"^(?:export\s+)?(?:default\s+)?(?:class|function|interface|type|const|let|var)\s+({symbol})\b", re.MULTILINE),
    ]

    for root, _, files in os.walk(workspace):
        if any(ignored in root for ignored in [".git", "node_modules", "__pycache__", ".venv", "venv", "env"]):
            continue

        for file in files:
            if not file.endswith(('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.c', '.cpp', '.h')):
                continue

            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError) as e:
                logger.debug(f"lsp_go_to_definition: skip {path}: {e}")
                continue

            for pattern in patterns:
                for match in pattern.finditer(content):
                    # Get line number
                    start_pos = match.start()
                    line_no = content.count('\n', 0, start_pos) + 1
                    
                    # Get snippet (line before, line, line after)
                    lines = content.split('\n')
                    start_idx = max(0, line_no - 2)
                    end_idx = min(len(lines), line_no + 2)
                    snippet = "\n".join(f"{i+1:4d} | {lines[i]}" for i in range(start_idx, end_idx))

                    rel_path = os.path.relpath(path, workspace)
                    results.append({
                        "file": rel_path,
                        "line": line_no,
                        "snippet": snippet
                    })
    return results

@tool
def lsp_go_to_definition(symbol: str) -> str:
    """Find the definition of a class, function, or type in the workspace (Go To Definition).
    
    Args:
        symbol: The exact name of the symbol to look up (e.g., 'AgentState', 'file_edit').
    """
    workspace = config.WORKSPACE_DIR
    results = _find_definition_regex(workspace, symbol)
    
    if not results:
        return f"❌ LSP: Could not find definition for '{symbol}' in workspace."
        
    output = []
    output.append(f"🔍 LSP Definition found for '{symbol}':")
    for res in results:
        output.append(f"\n📄 {res['file']} (Line {res['line']})")
        output.append("```\n" + res['snippet'] + "\n```")
        
    return truncate_output("\n".join(output))


@tool
def lsp_find_references(symbol: str) -> str:
    """Find all usages/references of a symbol across the entire workspace.
    
    Args:
        symbol: The exact symbol to search for.
    """
    workspace = config.WORKSPACE_DIR
    pattern = re.compile(fr"\b{re.escape(symbol)}\b")
    
    results = []
    for root, _, files in os.walk(workspace):
        if any(ignored in root for ignored in [".git", "node_modules", "__pycache__", ".venv", "venv", "env"]):
            continue

        for file in files:
            if not file.endswith(('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.c', '.cpp', '.h', '.md')):
                continue

            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except (OSError, UnicodeDecodeError) as e:
                logger.debug(f"lsp_find_references: skip {path}: {e}")
                continue

            file_matches = []
            for i, line in enumerate(lines):
                if pattern.search(line):
                    file_matches.append(f"{i+1:4d} | {line.rstrip()}")
            
            if file_matches:
                rel_path = os.path.relpath(path, workspace)
                results.append((rel_path, file_matches))
                
    if not results:
        return f"❌ LSP: No references found for '{symbol}'."
        
    output = [f"🔗 LSP References for '{symbol}':"]
    # Sort files by number of occurrences (descending)
    results.sort(key=lambda x: len(x[1]), reverse=True)
    
    for file, matches in results[:20]: # Limit to 20 files
        output.append(f"\n📄 {file} ({len(matches)} usages)")
        for m in matches[:5]: # Limit to 5 snippets per file
            output.append(m)
        if len(matches) > 5:
            output.append(f"     ... and {len(matches) - 5} more")
            
    if len(results) > 20:
        output.append(f"\n... and {len(results) - 20} more files.")
        
    return truncate_output("\n".join(output))
