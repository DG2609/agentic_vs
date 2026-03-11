#!/usr/bin/env python3
"""
ShadowDev Benchmark Runner

Runs agent tasks in headless mode and measures performance:
  - Wall time, tool call count, task completion checks.

Usage:
    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --tasks file_edit,search
    python benchmarks/run_benchmark.py --model gpt-4o --output results.json
    python benchmarks/run_benchmark.py --timeout 120
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path so imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import config
from agent.headless import run_headless, HeadlessResult


# ── Task definition ──────────────────────────────────────────

@dataclass
class BenchmarkTask:
    """A single benchmark task to run."""
    name: str
    prompt: str
    expected_tools: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    max_time_s: int = 60
    difficulty: str = "easy"


@dataclass
class TaskResult:
    """Result of running a single benchmark task."""
    task: str
    status: str = "fail"          # "pass" | "fail" | "error" | "timeout"
    elapsed_s: float = 0.0
    tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    expected_tools_hit: bool = False
    expected_files_found: bool = False
    errors: list[str] = field(default_factory=list)


# ── Built-in tasks ───────────────────────────────────────────

BUILTIN_TASKS: dict[str, BenchmarkTask] = {
    "file_edit": BenchmarkTask(
        name="file_edit",
        prompt=(
            "Create a Python file called hello.py in the workspace with a function "
            "greet(name) that returns f\"Hello, {name}!\". Then edit the file to add "
            "type hints: name should be str and the return type should be str."
        ),
        expected_tools=["file_write", "file_edit"],
        expected_files=["hello.py"],
        max_time_s=60,
        difficulty="easy",
    ),
    "search": BenchmarkTask(
        name="search",
        prompt=(
            "Find all function definitions in agent/nodes.py whose names start with "
            "an underscore. List every matching function name."
        ),
        expected_tools=["grep_search"],
        max_time_s=45,
        difficulty="easy",
    ),
    "multi_file": BenchmarkTask(
        name="multi_file",
        prompt=(
            "Create three Python files in the workspace for a simple Django-like CRUD app:\n"
            "1. models.py — a Task class with fields id (int), title (str), done (bool), "
            "and a to_dict() method.\n"
            "2. views.py — functions list_tasks(), create_task(title), update_task(id, done), "
            "delete_task(id) operating on an in-memory list of Task objects.\n"
            "3. urls.py — a routes dict mapping URL patterns to view functions: "
            "GET /tasks -> list_tasks, POST /tasks -> create_task, "
            "PUT /tasks/<id> -> update_task, DELETE /tasks/<id> -> delete_task."
        ),
        expected_tools=["file_write"],
        expected_files=["models.py", "views.py", "urls.py"],
        max_time_s=90,
        difficulty="medium",
    ),
    "test_run": BenchmarkTask(
        name="test_run",
        prompt=(
            "Create calculator.py in the workspace with functions add(a,b), subtract(a,b), "
            "multiply(a,b), divide(a,b) (raise ValueError on zero division). "
            "Then create test_calculator.py with pytest tests covering all four operations "
            "plus a test for divide-by-zero. Finally, run the tests and confirm they pass."
        ),
        expected_tools=["file_write", "run_tests"],
        expected_files=["calculator.py", "test_calculator.py"],
        max_time_s=120,
        difficulty="medium",
    ),
    "git_ops": BenchmarkTask(
        name="git_ops",
        prompt=(
            "Check the git status of this repository. Then show the 5 most recent commits. "
            "Then show the diff of the latest commit (HEAD~1..HEAD). "
            "Summarize the repository state."
        ),
        expected_tools=["git_status", "git_log", "git_diff"],
        max_time_s=45,
        difficulty="easy",
    ),
}


# ── Task loading from markdown ───────────────────────────────

def load_task_from_md(path: Path) -> Optional[BenchmarkTask]:
    """Parse a task .md file with YAML frontmatter into a BenchmarkTask."""
    try:
        import yaml
    except ImportError:
        return None

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict):
        return None

    body = parts[2].strip()
    name = meta.get("name", path.stem)

    # If we have a built-in task with matching name, prefer the built-in prompt
    # but merge metadata from the .md file
    if name in BUILTIN_TASKS:
        task = BUILTIN_TASKS[name]
        task.expected_tools = meta.get("expected_tools", task.expected_tools)
        task.max_time_s = meta.get("max_time_s", task.max_time_s)
        task.difficulty = meta.get("difficulty", task.difficulty)
        return task

    return BenchmarkTask(
        name=name,
        prompt=body,
        expected_tools=meta.get("expected_tools", []),
        max_time_s=meta.get("max_time_s", 60),
        difficulty=meta.get("difficulty", "easy"),
    )


def discover_tasks(tasks_dir: Path) -> dict[str, BenchmarkTask]:
    """Load task definitions from .md files, merged with built-in tasks."""
    tasks = dict(BUILTIN_TASKS)
    if tasks_dir.is_dir():
        for md_file in sorted(tasks_dir.glob("*.md")):
            task = load_task_from_md(md_file)
            if task:
                tasks[task.name] = task
    return tasks


# ── Run a single task ────────────────────────────────────────

async def run_task(task: BenchmarkTask, timeout_override: Optional[int] = None) -> TaskResult:
    """Execute a single benchmark task and return the result."""
    result = TaskResult(task=task.name)
    timeout = timeout_override or task.max_time_s

    try:
        headless: HeadlessResult = await run_headless(
            prompt=task.prompt,
            agent="coder",
            output_format="text",
            timeout=timeout,
        )

        result.elapsed_s = round(headless.elapsed, 2)
        result.tool_calls = len(headless.tool_calls)
        result.tools_used = list({tc["tool"] for tc in headless.tool_calls})
        result.errors = headless.errors

        # Check expected tools
        if task.expected_tools:
            used_names = {tc["tool"] for tc in headless.tool_calls}
            result.expected_tools_hit = all(t in used_names for t in task.expected_tools)
        else:
            result.expected_tools_hit = True

        # Check expected files
        if task.expected_files:
            workspace = Path(config.WORKSPACE_DIR)
            result.expected_files_found = all(
                (workspace / f).exists() for f in task.expected_files
            )
        else:
            result.expected_files_found = True

        # Determine status
        if headless.exit_code == 124:
            result.status = "timeout"
        elif headless.exit_code != 0:
            result.status = "error"
        elif result.expected_tools_hit and result.expected_files_found:
            result.status = "pass"
        else:
            result.status = "fail"

    except Exception as exc:
        result.status = "error"
        result.errors.append(f"{type(exc).__name__}: {exc}")

    return result


# ── Output formatting ────────────────────────────────────────

def print_summary_table(results: list[TaskResult]) -> None:
    """Print a summary table using rich."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="ShadowDev Benchmark Results", show_lines=True)

        table.add_column("Task", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Time (s)", justify="right")
        table.add_column("Tool Calls", justify="right")
        table.add_column("Complete", justify="center")

        status_styles = {
            "pass": "bold green",
            "fail": "bold red",
            "error": "bold red",
            "timeout": "bold yellow",
        }

        for r in results:
            complete = "Yes" if (r.expected_tools_hit and r.expected_files_found) else "No"
            table.add_row(
                r.task,
                f"[{status_styles.get(r.status, '')}]{r.status.upper()}[/]",
                f"{r.elapsed_s:.1f}",
                str(r.tool_calls),
                complete,
            )

        # Summary row
        passed = sum(1 for r in results if r.status == "pass")
        total = len(results)
        avg_time = sum(r.elapsed_s for r in results) / max(total, 1)
        total_tools = sum(r.tool_calls for r in results)

        console.print()
        console.print(table)
        console.print(f"\n  Passed: {passed}/{total}  |  Avg time: {avg_time:.1f}s  |  Total tool calls: {total_tools}")
        console.print()

    except ImportError:
        # Fallback: plain text
        print("\n--- Benchmark Results ---")
        print(f"{'Task':<14} {'Status':<10} {'Time':>8} {'Tools':>6} {'Complete':>9}")
        print("-" * 50)
        for r in results:
            complete = "Yes" if (r.expected_tools_hit and r.expected_files_found) else "No"
            print(f"{r.task:<14} {r.status.upper():<10} {r.elapsed_s:>7.1f}s {r.tool_calls:>5}  {complete:>8}")
        print()


def build_json_report(
    results: list[TaskResult],
    model: str,
    provider: str,
) -> dict:
    """Build a JSON-serializable report dict."""
    passed = sum(1 for r in results if r.status == "pass")
    total = len(results)
    avg_time = sum(r.elapsed_s for r in results) / max(total, 1)
    total_tools = sum(r.tool_calls for r in results)

    return {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "provider": provider,
        "results": [asdict(r) for r in results],
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "avg_time_s": round(avg_time, 2),
            "total_tool_calls": total_tools,
        },
    }


# ── CLI entrypoint ───────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ShadowDev Benchmark Runner — measure agent performance on coding tasks.",
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default="",
        help="Comma-separated list of task names to run (default: all). "
             "Available: file_edit, search, multi_file, test_run, git_ops",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Override the LLM model name (uses configured default if empty).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to write JSON results (e.g. results.json).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Per-task timeout in seconds (overrides task max_time_s). 0 = use task default.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    # Resolve model / provider for reporting
    provider = config.LLM_PROVIDER
    if args.model:
        model = args.model
        # Apply model override to the matching provider config
        model_attr = f"{provider.upper()}_MODEL"
        if hasattr(config, model_attr):
            setattr(config, model_attr, args.model)
    else:
        model_attr = f"{provider.upper()}_MODEL"
        model = getattr(config, model_attr, "unknown")

    # Discover tasks
    tasks_dir = Path(__file__).resolve().parent / "tasks"
    all_tasks = discover_tasks(tasks_dir)

    # Filter tasks
    if args.tasks:
        selected_names = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = [n for n in selected_names if n not in all_tasks]
        if unknown:
            print(f"Unknown tasks: {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(all_tasks.keys()))}", file=sys.stderr)
            return 1
        tasks_to_run = [all_tasks[n] for n in selected_names]
    else:
        tasks_to_run = list(all_tasks.values())

    timeout = args.timeout if args.timeout > 0 else None

    print(f"ShadowDev Benchmark Runner")
    print(f"  Provider: {provider}  |  Model: {model}  |  Tasks: {len(tasks_to_run)}")
    print()

    # Run tasks sequentially (each needs a clean agent state)
    results: list[TaskResult] = []
    for i, task in enumerate(tasks_to_run, 1):
        print(f"[{i}/{len(tasks_to_run)}] Running: {task.name} ({task.difficulty}) ...")
        task_result = await run_task(task, timeout_override=timeout)
        results.append(task_result)
        status_indicator = "PASS" if task_result.status == "pass" else task_result.status.upper()
        print(f"         {status_indicator} in {task_result.elapsed_s:.1f}s ({task_result.tool_calls} tool calls)")

    # Print summary
    print_summary_table(results)

    # Write JSON output
    if args.output:
        report = build_json_report(results, model=model, provider=provider)
        output_path = Path(args.output)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Results written to: {output_path}")

    # Exit code: 0 if all passed, 1 otherwise
    all_passed = all(r.status == "pass" for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
