"""
GitHub API integration tools.

Uses config.GITHUB_TOKEN for authentication.
Requires no extra dependencies — uses requests (already in requirements).

Repo auto-detection: if `repo` arg is omitted, parses `git remote get-url origin`
and extracts `owner/repo` from SSH (git@github.com:...) or HTTPS (https://github.com/...) URLs.
"""

import logging
import re
import subprocess
from typing import Optional

import requests
from langchain_core.tools import tool

import config
from models.tool_schemas import (
    GithubListIssuesArgs,
    GithubListPRsArgs,
    GithubGetPRArgs,
    GithubCreateIssueArgs,
    GithubCreatePRArgs,
    GithubCommentArgs,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_GH_URL_RE = re.compile(
    r"(?:git@github\.com:|https?://github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$"
)


# ── Internal helpers ──────────────────────────────────────────

def _headers() -> dict:
    """Build request headers. Raises if token is missing."""
    token = config.GITHUB_TOKEN
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not configured. Set it in .env or the GITHUB_TOKEN environment variable."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _detect_repo(repo: str) -> str:
    """Return repo as-is if provided; otherwise auto-detect from git remote."""
    if repo:
        return repo
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=config.WORKSPACE_DIR,
        )
        if result.returncode == 0:
            m = _GH_URL_RE.match(result.stdout.strip())
            if m:
                return m.group(1)
    except Exception:
        pass
    raise ValueError(
        "Could not auto-detect GitHub repo. Provide the 'repo' argument as 'owner/repo'."
    )


def _request(method: str, path: str, **kwargs) -> dict | list:
    """Make a GitHub API request; raise RuntimeError on failure."""
    url = f"{_API_BASE}{path}"
    try:
        resp = requests.request(
            method, url, headers=_headers(),
            timeout=config.TOOL_TIMEOUT, **kwargs
        )
    except requests.RequestException as e:
        raise RuntimeError(f"GitHub API request failed: {e}") from e

    if not resp.ok:
        try:
            msg = resp.json().get("message", resp.text[:200])
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"GitHub API error {resp.status_code}: {msg}")

    if resp.status_code == 204:
        return {}
    return resp.json()


def _fmt_issue(issue: dict) -> str:
    """Format one issue/PR summary line."""
    labels = ", ".join(lb["name"] for lb in issue.get("labels", []))
    assignee = (issue.get("assignee") or {}).get("login", "—")
    state = issue.get("state", "?")
    num = issue.get("number", "?")
    title = issue.get("title", "")
    url = issue.get("html_url", "")
    label_str = f" [{labels}]" if labels else ""
    return f"#{num} [{state}] {title} (assignee: {assignee}){label_str}\n  {url}"


# ── Read-only tools (Planner) ─────────────────────────────────

@tool(args_schema=GithubListIssuesArgs)
def github_list_issues(
    repo: str = "",
    state: str = "open",
    labels: str = "",
    assignee: str = "",
    per_page: int = 20,
) -> str:
    """List GitHub issues for a repository.

    Returns issue numbers, titles, labels, assignees, and links.
    Auto-detects repo from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        params: dict = {"state": state, "per_page": min(per_page, 100)}
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        issues = _request("GET", f"/repos/{repo}/issues", params=params)
        # issues endpoint returns PRs too — filter them out
        issues = [i for i in issues if "pull_request" not in i]
        if not issues:
            return f"No {state} issues found in {repo}."
        lines = [f"Issues in {repo} (state={state}, {len(issues)} shown):"]
        for issue in issues:
            lines.append("  " + _fmt_issue(issue))
        return "\n".join(lines)
    except Exception as e:
        return f"[github_list_issues error] {e}"


@tool(args_schema=GithubListPRsArgs)
def github_list_prs(
    repo: str = "",
    state: str = "open",
    base: str = "",
    per_page: int = 20,
) -> str:
    """List GitHub pull requests for a repository.

    Returns PR numbers, titles, source/target branches, and links.
    Auto-detects repo from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        params: dict = {"state": state, "per_page": min(per_page, 100)}
        if base:
            params["base"] = base
        prs = _request("GET", f"/repos/{repo}/pulls", params=params)
        if not prs:
            return f"No {state} PRs found in {repo}."
        lines = [f"Pull Requests in {repo} (state={state}, {len(prs)} shown):"]
        for pr in prs:
            head = pr.get("head", {}).get("ref", "?")
            base_ref = pr.get("base", {}).get("ref", "?")
            num = pr.get("number", "?")
            title = pr.get("title", "")
            url = pr.get("html_url", "")
            draft = " [DRAFT]" if pr.get("draft") else ""
            lines.append(f"  #{num} {title}{draft}\n    {head} → {base_ref}\n    {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"[github_list_prs error] {e}"


@tool(args_schema=GithubGetPRArgs)
def github_get_pr(
    pr_number: int,
    repo: str = "",
) -> str:
    """Get details of a specific GitHub pull request including its diff.

    Returns PR metadata (title, description, status checks) and file-level diff summary.
    """
    try:
        repo = _detect_repo(repo)
        pr = _request("GET", f"/repos/{repo}/pulls/{pr_number}")
        files = _request("GET", f"/repos/{repo}/pulls/{pr_number}/files", params={"per_page": 100})

        head = pr.get("head", {}).get("ref", "?")
        base_ref = pr.get("base", {}).get("ref", "?")
        state = pr.get("state", "?")
        merged = pr.get("merged", False)
        draft = pr.get("draft", False)
        title = pr.get("title", "")
        body = (pr.get("body") or "").strip()
        url = pr.get("html_url", "")

        status = "merged" if merged else ("draft" if draft else state)
        lines = [
            f"PR #{pr_number}: {title}",
            f"  Status : {status}",
            f"  Branch : {head} → {base_ref}",
            f"  URL    : {url}",
        ]
        if body:
            lines.append(f"  Body   : {body[:500]}{'...' if len(body) > 500 else ''}")

        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)
        changed = pr.get("changed_files", 0)
        lines.append(f"\n  Files changed: {changed} (+{additions} / -{deletions} lines)")

        if files:
            lines.append("  Changed files:")
            for f in files[:20]:
                fname = f.get("filename", "?")
                status_f = f.get("status", "?")
                a = f.get("additions", 0)
                d = f.get("deletions", 0)
                lines.append(f"    [{status_f}] {fname} (+{a}/-{d})")
            if len(files) > 20:
                lines.append(f"    ... and {len(files) - 20} more files")

        return "\n".join(lines)
    except Exception as e:
        return f"[github_get_pr error] {e}"


# ── Write tools (Coder) ───────────────────────────────────────

@tool(args_schema=GithubCreateIssueArgs)
def github_create_issue(
    title: str,
    body: str = "",
    labels: str = "",
    assignee: str = "",
    repo: str = "",
) -> str:
    """Create a new GitHub issue.

    Returns the URL and number of the created issue.
    Auto-detects repo from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        payload: dict = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = [lb.strip() for lb in labels.split(",") if lb.strip()]
        if assignee:
            payload["assignees"] = [assignee]

        result = _request("POST", f"/repos/{repo}/issues", json=payload)
        num = result.get("number")
        url = result.get("html_url", "")
        return f"Created issue #{num}: {title}\n  {url}"
    except Exception as e:
        return f"[github_create_issue error] {e}"


@tool(args_schema=GithubCreatePRArgs)
def github_create_pr(
    title: str,
    branch: str,
    base: str = "main",
    body: str = "",
    draft: bool = False,
    repo: str = "",
) -> str:
    """Create a new GitHub pull request.

    Returns the URL and number of the created PR.
    Auto-detects repo from git remote if not specified.
    The branch must already exist on the remote (use git_push first).
    """
    try:
        repo = _detect_repo(repo)
        payload: dict = {
            "title": title,
            "head": branch,
            "base": base,
            "draft": draft,
        }
        if body:
            payload["body"] = body

        result = _request("POST", f"/repos/{repo}/pulls", json=payload)
        num = result.get("number")
        url = result.get("html_url", "")
        draft_str = " [DRAFT]" if draft else ""
        return f"Created PR #{num}{draft_str}: {title}\n  {branch} → {base}\n  {url}"
    except Exception as e:
        return f"[github_create_pr error] {e}"


@tool(args_schema=GithubCommentArgs)
def github_comment(
    number: int,
    body: str,
    repo: str = "",
) -> str:
    """Add a comment to a GitHub issue or pull request.

    Works for both issues and PRs (they share the same comments API).
    Returns the URL of the created comment.
    """
    try:
        repo = _detect_repo(repo)
        result = _request(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        url = result.get("html_url", "")
        return f"Comment posted on #{number}.\n  {url}"
    except Exception as e:
        return f"[github_comment error] {e}"
