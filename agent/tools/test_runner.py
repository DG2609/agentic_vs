"""
Tool: run_tests — auto-detect and execute test suite.

Supports: pytest, jest/vitest, go test, cargo test, make test.
Parses structured output (pass/fail/error counts, failed test names).
"""
import os
import re
import subprocess
from langchain_core.tools import tool

import config
from agent.tools.utils import resolve_tool_path
from agent.tools.truncation import truncate_output
from models.tool_schemas import RunTestsArgs


# ── Framework detection ────────────────────────────────────────

_FRAMEWORK_MARKERS = [
    # (filename_to_check, framework_name)
    ("pytest.ini", "pytest"),
    ("setup.cfg", "pytest"),       # may contain [tool:pytest]
    ("pyproject.toml", "pytest"),  # may contain [tool.pytest.ini_options]
    ("conftest.py", "pytest"),
    ("Cargo.toml", "cargo"),
    ("go.mod", "go"),
    ("package.json", "jest"),      # refined below
    ("vitest.config.ts", "vitest"),
    ("vitest.config.js", "vitest"),
    ("Makefile", "make"),
]


def _detect_framework(work_dir: str) -> str:
    """Heuristic framework detection based on project files."""
    files_present = set(os.listdir(work_dir)) if os.path.isdir(work_dir) else set()

    # Check for vitest first (before jest)
    if "vitest.config.ts" in files_present or "vitest.config.js" in files_present:
        return "vitest"

    # Check package.json for jest vs vitest
    pkg_json = os.path.join(work_dir, "package.json")
    if os.path.isfile(pkg_json):
        try:
            import json
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "vitest" in deps:
                return "vitest"
            if "jest" in deps or "@jest/core" in deps:
                return "jest"
        except Exception:
            pass
        return "jest"  # fallback if package.json found

    for filename, fw in _FRAMEWORK_MARKERS:
        if filename in files_present:
            return fw

    # Recurse one level for conftest.py
    try:
        for entry in os.scandir(work_dir):
            if entry.is_dir() and entry.name not in {"node_modules", ".git", "__pycache__", "venv"}:
                if "conftest.py" in os.listdir(entry.path):
                    return "pytest"
    except OSError:
        pass

    return "unknown"


# ── Command builders ───────────────────────────────────────────

def _build_command(framework: str, work_dir: str, path: str, pattern: str) -> list[str] | None:
    """Build the test command for the given framework."""
    rel_path = path  # already resolved by caller

    if framework == "pytest":
        cmd = ["python", "-m", "pytest", "--tb=short", "-v"]
        if rel_path:
            cmd.append(rel_path)
        if pattern:
            cmd += ["-k", pattern]
        return cmd

    elif framework == "jest":
        cmd = ["npx", "jest", "--no-coverage"]
        if rel_path:
            cmd.append(rel_path)
        if pattern:
            cmd += ["--testNamePattern", pattern]
        return cmd

    elif framework == "vitest":
        cmd = ["npx", "vitest", "run"]
        if rel_path:
            cmd.append(rel_path)
        if pattern:
            cmd += ["--reporter=verbose", "-t", pattern]
        return cmd

    elif framework == "cargo":
        cmd = ["cargo", "test"]
        if pattern:
            cmd.append(pattern)
        return cmd

    elif framework == "go":
        test_path = "./..."
        if rel_path:
            test_path = rel_path if rel_path.startswith(".") else f"./{rel_path}"
        cmd = ["go", "test", "-v", test_path]
        if pattern:
            cmd += ["-run", pattern]
        return cmd

    elif framework == "make":
        return ["make", "test"]

    return None


# ── Output parsers ─────────────────────────────────────────────

def _parse_pytest_output(output: str) -> dict:
    """Parse pytest output into structured summary."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}

    # Final summary line: "5 passed, 2 failed, 1 error in 3.21s"
    summary_pat = re.compile(
        r"(\d+) passed|(\d+) failed|(\d+) error|(\d+) skipped", re.IGNORECASE
    )
    for m in summary_pat.finditer(output):
        if m.group(1):
            summary["passed"] += int(m.group(1))
        elif m.group(2):
            summary["failed"] += int(m.group(2))
        elif m.group(3):
            summary["errors"] += int(m.group(3))
        elif m.group(4):
            summary["skipped"] += int(m.group(4))

    # Extract failed test names: "FAILED tests/test_foo.py::test_bar"
    for m in re.finditer(r"FAILED\s+([\w/\\.:]+)", output):
        summary["failed_tests"].append(m.group(1))

    return summary


def _parse_jest_output(output: str) -> dict:
    """Parse jest/vitest output into structured summary."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}

    # "Tests: 3 failed, 12 passed, 15 total"
    tests_line = re.search(r"Tests:\s+(.+)", output)
    if tests_line:
        for m in re.finditer(r"(\d+)\s+(failed|passed|skipped)", tests_line.group(1)):
            count, status = int(m.group(1)), m.group(2)
            if status == "failed":
                summary["failed"] = count
            elif status == "passed":
                summary["passed"] = count
            elif status == "skipped":
                summary["skipped"] = count

    # "✕ test name" or "× test name"
    for m in re.finditer(r"[✕×✗]\s+(.+)", output):
        summary["failed_tests"].append(m.group(1).strip())

    return summary


def _parse_go_output(output: str) -> dict:
    """Parse go test output."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}

    for line in output.splitlines():
        if line.startswith("--- FAIL:"):
            summary["failed"] += 1
            m = re.match(r"--- FAIL: (\S+)", line)
            if m:
                summary["failed_tests"].append(m.group(1))
        elif line.startswith("--- PASS:"):
            summary["passed"] += 1
        elif line.startswith("--- SKIP:"):
            summary["skipped"] += 1
        elif line.startswith("FAIL"):
            if summary["failed"] == 0:
                summary["errors"] += 1

    return summary


def _parse_cargo_output(output: str) -> dict:
    """Parse cargo test output."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}

    # "test result: FAILED. 3 passed; 2 failed; 0 ignored"
    m = re.search(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", output)
    if m:
        summary["passed"] = int(m.group(1))
        summary["failed"] = int(m.group(2))
        summary["skipped"] = int(m.group(3))

    for m in re.finditer(r"test (\S+) \.\.\. FAILED", output):
        summary["failed_tests"].append(m.group(1))

    return summary


def _parse_output(framework: str, output: str) -> dict:
    """Dispatch to framework-specific parser."""
    if framework == "pytest":
        return _parse_pytest_output(output)
    elif framework in ("jest", "vitest"):
        return _parse_jest_output(output)
    elif framework == "go":
        return _parse_go_output(output)
    elif framework == "cargo":
        return _parse_cargo_output(output)
    return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failed_tests": []}


def _format_summary(framework: str, stats: dict, returncode: int, duration: float) -> str:
    """Format test summary header."""
    total = stats["passed"] + stats["failed"] + stats.get("errors", 0)
    status = "✅ PASSED" if returncode == 0 else "❌ FAILED"

    parts = [f"{status} ({framework}) — {duration:.1f}s"]
    if total > 0:
        parts.append(
            f"  {stats['passed']} passed, {stats['failed']} failed"
            + (f", {stats['errors']} errors" if stats.get('errors') else "")
            + (f", {stats['skipped']} skipped" if stats.get('skipped') else "")
        )
    if stats["failed_tests"]:
        parts.append("\n  Failed tests:")
        for t in stats["failed_tests"][:10]:
            parts.append(f"    • {t}")
        if len(stats["failed_tests"]) > 10:
            parts.append(f"    ... and {len(stats['failed_tests']) - 10} more")

    return "\n".join(parts)


# ── Tool ──────────────────────────────────────────────────────

@tool(args_schema=RunTestsArgs)
def run_tests(
    path: str = "",
    framework: str = "auto",
    pattern: str = "",
    timeout: int = 60,
) -> str:
    """Run the test suite and return a structured pass/fail summary.

    Auto-detects pytest, jest, vitest, cargo, or go test from project files.
    Parses output to extract passed/failed counts and names of failing tests.

    Args:
        path: Sub-directory or specific test file. Empty = workspace root.
        framework: Test framework to use. 'auto' detects automatically.
        pattern: Filter pattern for test names (framework-specific syntax).
        timeout: Max execution time in seconds.

    Returns:
        Structured summary with pass/fail counts, failed test names, and raw output.
    """
    import time

    # Resolve working directory
    work_dir = config.WORKSPACE_DIR
    if path:
        resolved = resolve_tool_path(path)
        if os.path.isdir(resolved):
            work_dir = resolved
        elif os.path.isfile(resolved):
            work_dir = os.path.dirname(resolved)
            path = os.path.basename(resolved)
        else:
            return f"❌ Error: path '{path}' not found."

    # Detect framework
    if framework == "auto":
        framework = _detect_framework(work_dir)
        if framework == "unknown":
            return (
                "❌ Could not auto-detect test framework.\n"
                "No pytest.ini, Cargo.toml, go.mod, or package.json found.\n"
                "Use the 'framework' parameter to specify one: pytest|jest|vitest|cargo|go|make"
            )

    # Build command
    cmd = _build_command(framework, work_dir, path, pattern)
    if cmd is None:
        return f"❌ Unsupported framework: '{framework}'"

    # Execute
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        duration = time.monotonic() - start
    except subprocess.TimeoutExpired:
        return f"❌ Test timed out after {timeout}s. Use a longer timeout or narrow the test path."
    except FileNotFoundError:
        return (
            f"❌ Command not found: `{cmd[0]}`\n"
            f"Make sure '{framework}' is installed and available in PATH."
        )
    except Exception as e:
        return f"❌ Failed to run tests: {e}"

    # Combine stdout + stderr
    raw_output = result.stdout
    if result.stderr:
        raw_output += "\n--- stderr ---\n" + result.stderr

    # Parse + format summary
    stats = _parse_output(framework, raw_output)
    summary_header = _format_summary(framework, stats, result.returncode, duration)

    output = f"{summary_header}\n\n--- Output ---\n{raw_output}"
    return truncate_output(output)
