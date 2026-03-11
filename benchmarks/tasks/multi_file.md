---
name: multi_file
expected_tools:
  - file_write
  - file_edit
max_time_s: 90
difficulty: medium
---

# Multi-File Benchmark

Create three files (`models.py`, `views.py`, `urls.py`) for a simple Django-like CRUD app.

## Instructions

1. Create `models.py` with a `Task` class that has fields: `id` (int), `title` (str), `done` (bool), and a `to_dict()` method.
2. Create `views.py` with functions: `list_tasks()`, `create_task(title)`, `update_task(id, done)`, `delete_task(id)`. Each function should operate on an in-memory list of Task objects.
3. Create `urls.py` with a `routes` dictionary mapping URL patterns to view functions: `GET /tasks` -> `list_tasks`, `POST /tasks` -> `create_task`, `PUT /tasks/<id>` -> `update_task`, `DELETE /tasks/<id>` -> `delete_task`.

## Acceptance criteria

- All three files (`models.py`, `views.py`, `urls.py`) exist in the workspace.
- `models.py` defines a `Task` class with the specified fields.
- `views.py` defines all four CRUD functions.
- `urls.py` maps routes to view functions.
