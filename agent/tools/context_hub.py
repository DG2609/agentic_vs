"""
Context Hub integration — curated API docs for coding agents.

Wraps the `chub` CLI (npm: @aisuite/chub) to give the agent access to
68+ curated API/SDK docs (OpenAI, Stripe, AWS, Anthropic, etc.)
plus persistent annotations and feedback.

Install: npm install -g @aisuite/chub

Tools:
    chub_search  — Search docs/skills by query
    chub_get     — Fetch doc content by ID
    chub_annotate — Save/read persistent notes on docs
    chub_feedback — Rate docs for maintainers
"""

import json
import logging
import shutil
import subprocess
from typing import Optional

from langchain_core.tools import tool

from agent.tools.truncation import truncate_output

logger = logging.getLogger(__name__)

_CHUB_PATH: Optional[str] = None


def _find_chub() -> str:
    """Find the chub CLI binary."""
    global _CHUB_PATH
    if _CHUB_PATH:
        return _CHUB_PATH

    # Check if installed globally
    path = shutil.which("chub")
    if path:
        _CHUB_PATH = path
        return path

    # Fallback: use npx
    _CHUB_PATH = "npx"
    return _CHUB_PATH


def _run_chub(args: list[str], timeout: int = 30) -> dict:
    """Run a chub CLI command and return parsed result.

    Returns:
        {"ok": True, "output": str, "data": dict|None}
        or {"ok": False, "error": str}
    """
    chub = _find_chub()
    if chub == "npx":
        cmd = ["npx", "-y", "@aisuite/chub"] + args
    else:
        cmd = [chub] + args

    # Always request JSON when available
    if "--json" not in args and args[0] not in ("annotate",):
        cmd.append("--json")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            error_msg = stderr or output or f"chub exited with code {result.returncode}"
            return {"ok": False, "error": error_msg}

        # Try to parse JSON
        data = None
        if output:
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                pass

        return {"ok": True, "output": output, "data": data}

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"chub command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "error": "chub CLI not found. Install with: npm install -g @aisuite/chub"}


@tool
def chub_search(query: str = "", tags: str = "") -> str:
    """Search Context Hub for API documentation and skills.

    Searches 68+ curated API docs (OpenAI, Stripe, AWS, Anthropic, Firebase,
    MongoDB, Redis, Slack, Discord, etc.) and coding skills/recipes.

    Use this BEFORE writing code that calls external APIs — fetch real docs
    instead of guessing from training data.

    Args:
        query: Search term (e.g. "stripe payments", "openai chat").
               Empty string lists all available entries.
        tags: Comma-separated tag filter (e.g. "ai,llm" or "payments").
    """
    args = ["search"]
    if query:
        args.append(query)
    if tags:
        args.extend(["--tags", tags])

    result = _run_chub(args)
    if not result["ok"]:
        return f"Error: {result['error']}"

    data = result.get("data")
    if data and isinstance(data, dict):
        results = data.get("results", [])
        total = data.get("total", len(results))
        if not results:
            return f"No results for '{query}'. Try a broader term or run with empty query to list all."

        lines = [f"Found {total} result(s):\n"]
        for r in results[:20]:
            rid = r.get("id", "?")
            rtype = r.get("type", "doc")
            desc = r.get("description", "")[:80]
            langs = ""
            if r.get("languages"):
                lang_list = [l.get("language", "") for l in r["languages"]] if isinstance(r["languages"], list) else []
                if lang_list:
                    langs = f" [{', '.join(lang_list)}]"
            tags_str = ""
            if r.get("tags"):
                tags_str = f" tags:{','.join(r['tags'][:5])}"
            lines.append(f"  {rid} ({rtype}){langs} — {desc}{tags_str}")

        if total > 20:
            lines.append(f"\n  ... and {total - 20} more. Refine your search.")
        lines.append("\nUse chub_get with an ID to fetch the full documentation.")
        return "\n".join(lines)

    return result.get("output", "No results.")


@tool
def chub_get(
    entry_id: str,
    lang: str = "",
    version: str = "",
    file: str = "",
    full: bool = False,
) -> str:
    """Fetch API documentation or skill content from Context Hub.

    Returns curated, up-to-date documentation for external APIs/SDKs.
    Use this instead of relying on training knowledge when writing code
    against external services.

    Args:
        entry_id: Doc/skill ID from chub_search (e.g. "openai/chat-api", "stripe/api").
        lang: Language variant — "py", "js", "ts", etc. Required if doc has multiple languages.
        version: Specific version (default: latest recommended).
        file: Fetch a specific reference file (e.g. "references/auth.md").
        full: If True, fetch all files (entry + references), not just entry point.
    """
    args = ["get", entry_id]
    if lang:
        args.extend(["--lang", lang])
    if version:
        args.extend(["--version", version])
    if file:
        args.extend(["--file", file])
    if full:
        args.append("--full")

    result = _run_chub(args, timeout=60)
    if not result["ok"]:
        return f"Error: {result['error']}"

    data = result.get("data")
    if data and isinstance(data, dict):
        content = data.get("content", "")
        annotation = data.get("annotation")
        additional = data.get("additionalFiles", [])

        parts = [content]
        if annotation:
            note = annotation.get("note", "")
            parts.append(f"\n---\n[Agent note] {note}")
        if additional:
            parts.append(f"\n---\nAdditional reference files available: {', '.join(additional)}")
            parts.append("Use chub_get with file='<path>' to fetch specific references.")

        return truncate_output("\n".join(parts))

    return truncate_output(result.get("output", "No content returned."))


@tool
def chub_annotate(entry_id: str, note: str = "", clear: bool = False, list_all: bool = False) -> str:
    """Save or read persistent notes on API docs (agent learning).

    Annotations persist across sessions and appear automatically on future
    chub_get calls. Use this to record gotchas, workarounds, version quirks,
    or project-specific details discovered while using an API.

    Args:
        entry_id: Doc/skill ID to annotate (e.g. "stripe/api"). Ignored if list_all=True.
        note: Note to save. Empty string reads the current annotation.
        clear: If True, remove the annotation for this entry.
        list_all: If True, list all existing annotations.
    """
    if list_all:
        args = ["annotate", "--list", "--json"]
    elif clear:
        args = ["annotate", entry_id, "--clear"]
    elif note:
        args = ["annotate", entry_id, note]
    else:
        # Read current annotation
        args = ["annotate", entry_id, "--json"]

    result = _run_chub(args)
    if not result["ok"]:
        return f"Error: {result['error']}"

    return result.get("output", "Done.")


@tool
def chub_feedback(entry_id: str, rating: str, comment: str = "", labels: str = "") -> str:
    """Rate an API doc or skill for maintainers (up/down).

    Feedback helps doc authors improve quality. Ask the user before sending.

    Args:
        entry_id: Doc/skill ID (e.g. "stripe/api").
        rating: "up" or "down".
        comment: Optional freetext comment.
        labels: Comma-separated labels. Valid: accurate, well-structured, helpful,
                good-examples, outdated, inaccurate, incomplete, wrong-examples,
                wrong-version, poorly-structured.
    """
    if rating not in ("up", "down"):
        return "Error: rating must be 'up' or 'down'"

    args = ["feedback", entry_id, rating]
    if comment:
        args.append(comment)
    if labels:
        for label in labels.split(","):
            label = label.strip()
            if label:
                args.extend(["--label", label])

    result = _run_chub(args)
    if not result["ok"]:
        return f"Error: {result['error']}"

    return result.get("output", f"Feedback sent: {rating} for {entry_id}")
