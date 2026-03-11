---
name: file_edit
expected_tools:
  - file_write
  - file_edit
max_time_s: 60
difficulty: easy
---

# File Edit Benchmark

Create a Python file `hello.py` with a `greet(name)` function, then edit it to add type hints.

## Instructions

1. Create a file called `hello.py` in the workspace with the following content:

```python
def greet(name):
    """Return a greeting message."""
    return f"Hello, {name}!"
```

2. Edit the file to add type hints to the function signature and return type:

```python
def greet(name: str) -> str:
    """Return a greeting message."""
    return f"Hello, {name}!"
```

## Acceptance criteria

- The file `hello.py` exists in the workspace.
- The function has type annotations for the `name` parameter (`str`) and return type (`str`).
