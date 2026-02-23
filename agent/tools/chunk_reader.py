import os
from pydantic import BaseModel, Field
from langchain_core.tools import tool
import config

class ReadFileChunkInput(BaseModel):
    path: str = Field(description="Relative path to the file to read")
    start_line: int = Field(description="1-indexed line number to start reading from")
    end_line: int = Field(description="1-indexed line number to end reading")

@tool("read_file_chunk", args_schema=ReadFileChunkInput)
def read_file_chunk(path: str, start_line: int, end_line: int) -> str:
    """
    Read a specific range of lines from a file. 
    Use this when you only need to look at a small part of a massive file (e.g. 50-100 lines at a time) to save context.
    1-indexed. Inclusive of start and end lines.
    """
    if start_line > end_line:
        return f"Error: start_line ({start_line}) cannot be greater than end_line ({end_line})"
    if start_line < 1:
         return f"Error: start_line must be >= 1"

    full_path = config.WORKSPACE_DIR / path
    if not full_path.is_file():
        return f"Error: File '{path}' not found."

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        if start_line > total_lines:
             return f"Error: start_line ({start_line}) is beyond EOF ({total_lines} total lines)"
        
        # Adjust end_line if it exceeds total lines
        actual_end = min(end_line, total_lines)
        
        # 1-indexed to 0-indexed slice
        chunk_lines = lines[start_line - 1 : actual_end]
        
        header = f"--- Reading {path} (Lines {start_line} to {actual_end} of {total_lines}) ---\n"
        
        # Add line numbers to output to help the LLM navigate
        formatted_chunk = "".join([f"{start_line + i}: {line}" for i, line in enumerate(chunk_lines)])
        
        return header + formatted_chunk

    except Exception as e:
        return f"Error reading chunk from '{path}': {str(e)}"
