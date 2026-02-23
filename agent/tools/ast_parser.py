import os
import ast
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import config

import re

class GetFileOutlineInput(BaseModel):
    path: str = Field(description="Relative path to the file to analyze (supports .py, .ts, .js, .tsx, .jsx, .xml, .html)")

@tool("get_file_outline", args_schema=GetFileOutlineInput)
def get_file_outline(path: str) -> str:
    """
    Get the outline of a file (Classes, Methods, Functions, XML Tags) without reading the entire content.
    Prevents overflowing the LLM Context window on files with thousands of lines. 
    Supports Python, JavaScript/TypeScript, and XML/HTML.
    """
    from pathlib import Path
    import os
    
    # Try workspace first, then fall back to base project dir
    workspace_path = Path(config.WORKSPACE_DIR) / path.strip("/\\")
    base_path = Path(config.BASE_DIR) / path.strip("/\\")
    
    if workspace_path.is_file():
        full_path = workspace_path
    elif base_path.is_file():
        full_path = base_path
    else:
        return f"Error: File '{path}' not found in workspace or base directory."

    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            lines = content.split('\n')

        outline = []
        
        # --- Python (Using AST) ---
        if path.endswith('.py'):
            try:
                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        outline.append(f"class {node.name}: (Line {node.lineno})")
                        for item in node.body:
                            if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                                outline.append(f"    def {item.name}: (Line {item.lineno})")
                    elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                        outline.append(f"def {node.name}: (Line {node.lineno})")
            except Exception as e:
                return f"Syntax Error while parsing Python: {e}"

        # --- XML / HTML (Using Regex for main tags) ---
        elif path.endswith(('.xml', '.html', '.htm', '.svg')):
            tag_limit = 100
            count = 0
            # Matches <tag id="foo"> or <tag> but ignores closing tags
            pattern = re.compile(r'^\s*<([a-zA-Z0-9_\-]+)([^>]*)>')
            for idx, line in enumerate(lines):
                match = pattern.search(line)
                if match:
                    tag_name = match.group(1)
                    attrs = match.group(2).strip()
                    # Filter out spam tags like <br>, <li>, etc in HTML if needed, but for now show all.
                    # Limit output to prevent huge context on data XMLs
                    if len(attrs) > 40:
                        attrs = attrs[:40] + '...'
                    outline.append(f"<{tag_name} {attrs}> (Line {idx + 1})")
                    count += 1
                    if count >= tag_limit:
                        outline.append(f"... (Truncated at {tag_limit} XML elements to save context)")
                        break

        # --- JS / TS / JSX / TSX (Regex heuristics for classes and functions) ---
        elif path.endswith(('.js', '.ts', '.jsx', '.tsx')):
            # Heuristic regex patterns for common JS/TS constructs
            class_pattern = re.compile(r'^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_]+)')
            func_pattern = re.compile(r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)')
            const_func_pattern = re.compile(r'^(?:export\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s+)?(?:function|\()')

            for idx, line in enumerate(lines):
                c_match = class_pattern.search(line.strip())
                f_match = func_pattern.search(line.strip())
                cf_match = const_func_pattern.search(line.strip())

                if c_match:
                    outline.append(f"class {c_match.group(1)} (Line {idx + 1})")
                elif f_match:
                    outline.append(f"function {f_match.group(1)} (Line {idx + 1})")
                elif cf_match:
                    outline.append(f"const/func {cf_match.group(1)} (Line {idx + 1})")

        else:
            return f"Error: File type for '{path}' is not supported by the AST/Regex parser."

        if not outline:
            return f"File '{path}' parsed successfully but contains no recognizable classes, functions, or major tags."

        header = f"--- Outline for {path} ---\n"
        return header + "\n".join(outline)
    
    except Exception as e:
        return f"Error analyzing '{path}': {str(e)}"
