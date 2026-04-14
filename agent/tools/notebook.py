"""
Tool: notebook_edit — read and edit Jupyter notebook (.ipynb) cells.

Supported actions:
    read   — list all cells with indices, types, and content preview
    edit   — replace the source of a cell at a given index (clears outputs for code cells)
    insert — insert a new cell before a given index
    delete — remove a cell at a given index
"""
import json
from pathlib import Path
from langchain_core.tools import tool
from agent.tools.truncation import truncate_output
from agent.tools.utils import resolve_tool_path
from models.tool_schemas import NotebookEditArgs


@tool(args_schema=NotebookEditArgs)
def notebook_edit(
    notebook_path: str,
    action: str = "read",
    cell_index: int = 0,
    cell_type: str = "code",
    source: str = "",
) -> str:
    """Read or edit cells in a Jupyter notebook (.ipynb file).

    Use 'read' to list all cells with their indices, types, and content.
    Use 'edit' to replace the content of a cell at a given index.
    Use 'insert' to insert a new cell before the given index.
    Use 'delete' to remove the cell at the given index.
    """
    resolved = resolve_tool_path(notebook_path)
    path = Path(resolved)

    if not path.exists():
        return f"Error: notebook not found: {notebook_path}"
    if path.suffix.lower() != ".ipynb":
        return f"Error: not a Jupyter notebook (must be .ipynb): {notebook_path}"

    try:
        nb = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"Error reading notebook: {e}"

    cells = nb.get("cells", [])

    if action == "read":
        lines = [f"Notebook: {notebook_path} ({len(cells)} cells)\n"]
        for i, cell in enumerate(cells):
            ctype = cell.get("cell_type", "unknown")
            src = "".join(cell.get("source", []))
            preview = src[:200] + "..." if len(src) > 200 else src
            lines.append(f"[{i}] {ctype}:\n{preview}\n")
        return truncate_output("\n".join(lines))

    elif action == "edit":
        if cell_index >= len(cells):
            return (
                f"Error: cell index {cell_index} out of range "
                f"(notebook has {len(cells)} cells)"
            )
        cells[cell_index]["source"] = source
        # Clear outputs for code cells on edit
        if cells[cell_index].get("cell_type") == "code":
            cells[cell_index]["outputs"] = []
            cells[cell_index]["execution_count"] = None
        nb["cells"] = cells
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"Cell {cell_index} updated."

    elif action == "insert":
        new_cell: dict = {
            "cell_type": cell_type,
            "source": source,
            "metadata": {},
        }
        if cell_type == "code":
            new_cell["outputs"] = []
            new_cell["execution_count"] = None
        cells.insert(cell_index, new_cell)
        nb["cells"] = cells
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"New {cell_type} cell inserted at index {cell_index}."

    elif action == "delete":
        if cell_index >= len(cells):
            return f"Error: cell index {cell_index} out of range (notebook has {len(cells)} cells)"
        removed = cells.pop(cell_index)
        nb["cells"] = cells
        path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        ctype = removed.get("cell_type", "unknown")
        return f"Cell {cell_index} ({ctype}) deleted."

    return f"Error: unknown action '{action}'"
