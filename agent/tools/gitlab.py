"""
GitLab API integration tools.

Uses config.GITLAB_TOKEN for authentication and config.GITLAB_INSTANCE_URL
as the base (default: https://gitlab.com).

Supports all GitLab instances (SaaS, self-hosted, GitLab CE/EE).
Repo auto-detection: parses `git remote get-url origin` for SSH / HTTPS GitLab URLs.
Note: GitLab calls pull requests "Merge Requests" (MRs).
"""

import logging
import re
import subprocess
from urllib.parse import quote

import requests
from langchain_core.tools import tool

import config
from models.tool_schemas import (
    GitlabListIssuesArgs,
    GitlabListMRsArgs,
    GitlabGetMRArgs,
    GitlabCreateIssueArgs,
    GitlabCreateMRArgs,
    GitlabCommentArgs,
)

logger = logging.getLogger(__name__)

# Matches:  git@gitlab.com:owner/repo.git
#           https://gitlab.com/owner/group/repo.git
#           https://mygitlab.corp/ns/sub/repo  (nested namespaces)
_GL_SSH_RE = re.compile(r"git@([^:]+):(.+?)(?:\.git)?$")
_GL_HTTP_RE = re.compile(r"https?://([^/]+)/(.+?)(?:\.git)?$")


# ── Internal helpers ──────────────────────────────────────────

def _api_base() -> str:
    """Return the GitLab API v4 base URL."""
    base = (config.GITLAB_INSTANCE_URL or "https://gitlab.com").rstrip("/")
    return f"{base}/api/v4"


def _headers() -> dict:
    """Build request headers. Raises if token is missing."""
    token = config.GITLAB_TOKEN
    if not token:
        raise RuntimeError(
            "GITLAB_TOKEN is not configured. Set it in .env or the GITLAB_TOKEN environment variable."
        )
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }


def _encode_repo(repo: str) -> str:
    """URL-encode 'namespace/project' → 'namespace%2Fproject' for GitLab paths."""
    return quote(repo, safe="")


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
        if result.returncode != 0:
            raise ValueError("git remote returned non-zero")

        url = result.stdout.strip()
        instance_host = (config.GITLAB_INSTANCE_URL or "https://gitlab.com").rstrip("/")
        instance_host = re.sub(r"^https?://", "", instance_host)

        # Try SSH: git@host:namespace/project.git
        m = _GL_SSH_RE.match(url)
        if m and m.group(1) == instance_host:
            return m.group(2)

        # Try HTTPS: https://host/namespace/project
        m = _GL_HTTP_RE.match(url)
        if m and m.group(1) == instance_host:
            return m.group(2)

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    except ValueError:
        pass

    raise ValueError(
        "Could not auto-detect GitLab repo. Provide the 'repo' argument as 'namespace/project'."
    )


def _request(method: str, path: str, **kwargs) -> dict | list:
    """Make a GitLab API request with rate-limit retry; raise RuntimeError on failure."""
    import time as _time

    url = f"{_api_base()}{path}"
    _MAX_RETRIES = 3
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(
                method, url, headers=_headers(),
                timeout=config.TOOL_TIMEOUT, **kwargs
            )
        except requests.RequestException as e:
            raise RuntimeError(f"GitLab API request failed: {e}") from e

        # Handle rate limiting (429 Too Many Requests)
        if resp.status_code == 429:
            if attempt < _MAX_RETRIES - 1:
                retry_after = resp.headers.get("Retry-After", "")
                wait = 60  # default
                if retry_after:
                    try:
                        wait = max(0, int(retry_after))
                    except ValueError:
                        pass
                if wait > 300:
                    break  # refuse to wait > 5 minutes
                logger.warning("[gitlab] Rate limited — retrying in %ds", wait)
                _time.sleep(wait)
                continue

        if not resp.ok:
            try:
                msg = resp.json().get("message", resp.text[:200])
                if isinstance(msg, list):
                    msg = "; ".join(msg)
            except Exception:
                msg = resp.text[:200]
            # Provide actionable error messages for common status codes
            if resp.status_code == 401:
                raise RuntimeError("GitLab API error 401: Unauthorized — check GITLAB_TOKEN")
            if resp.status_code == 403:
                raise RuntimeError(f"GitLab API error 403: Forbidden — {msg}")
            if resp.status_code == 404:
                raise RuntimeError(f"GitLab API error 404: Not found — {msg}")
            if resp.status_code == 422:
                raise RuntimeError(f"GitLab API error 422: Unprocessable — {msg}")
            raise RuntimeError(f"GitLab API error {resp.status_code}: {msg}")

        if resp.status_code == 204:
            return {}
        return resp.json()

    # All retries exhausted
    raise RuntimeError("GitLab API: rate limit exceeded and retry window too long")


def _fmt_issue(issue: dict) -> str:
    """Format one issue summary line."""
    labels = ", ".join(lb["name"] if isinstance(lb, dict) else lb for lb in issue.get("labels", []))
    assignee = (issue.get("assignee") or {}).get("username", "—")
    state = issue.get("state", "?")
    iid = issue.get("iid", "?")
    title = issue.get("title", "")
    url = issue.get("web_url", "")
    label_str = f" [{labels}]" if labels else ""
    return f"#{iid} [{state}] {title} (assignee: {assignee}){label_str}\n  {url}"


# ── Read-only tools (Planner) ─────────────────────────────────

@tool(args_schema=GitlabListIssuesArgs)
def gitlab_list_issues(
    repo: str = "",
    state: str = "opened",
    labels: str = "",
    assignee: str = "",
    per_page: int = 20,
) -> str:
    """List GitLab issues for a project.

    Returns issue numbers, titles, labels, assignees, and links.
    Auto-detects project from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        params: dict = {"state": state, "per_page": min(per_page, 100)}
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee_username"] = assignee
        issues = _request("GET", f"/projects/{rid}/issues", params=params)
        if not issues:
            return f"No {state} issues found in {repo}."
        lines = [f"Issues in {repo} (state={state}, {len(issues)} shown):"]
        for issue in issues:
            lines.append("  " + _fmt_issue(issue))
        return "\n".join(lines)
    except Exception as e:
        return f"[gitlab_list_issues error] {e}"


@tool(args_schema=GitlabListMRsArgs)
def gitlab_list_mrs(
    repo: str = "",
    state: str = "opened",
    target_branch: str = "",
    per_page: int = 20,
) -> str:
    """List GitLab merge requests (MRs) for a project.

    Returns MR numbers, titles, source/target branches, and links.
    Auto-detects project from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        params: dict = {"state": state, "per_page": min(per_page, 100)}
        if target_branch:
            params["target_branch"] = target_branch
        mrs = _request("GET", f"/projects/{rid}/merge_requests", params=params)
        if not mrs:
            return f"No {state} MRs found in {repo}."
        lines = [f"Merge Requests in {repo} (state={state}, {len(mrs)} shown):"]
        for mr in mrs:
            src = mr.get("source_branch", "?")
            tgt = mr.get("target_branch", "?")
            iid = mr.get("iid", "?")
            title = mr.get("title", "")
            url = mr.get("web_url", "")
            draft = " [DRAFT]" if mr.get("draft") or mr.get("work_in_progress") else ""
            lines.append(f"  !{iid} {title}{draft}\n    {src} → {tgt}\n    {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"[gitlab_list_mrs error] {e}"


@tool(args_schema=GitlabGetMRArgs)
def gitlab_get_mr(
    mr_number: int,
    repo: str = "",
) -> str:
    """Get details of a specific GitLab merge request including its diff summary.

    Returns MR metadata (title, description, status) and changed files list.
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        mr = _request("GET", f"/projects/{rid}/merge_requests/{mr_number}")
        changes = _request("GET", f"/projects/{rid}/merge_requests/{mr_number}/changes")

        src = mr.get("source_branch", "?")
        tgt = mr.get("target_branch", "?")
        state = mr.get("state", "?")
        draft = mr.get("draft") or mr.get("work_in_progress", False)
        title = mr.get("title", "")
        description = (mr.get("description") or "").strip()
        url = mr.get("web_url", "")

        status = "draft" if draft else state
        lines = [
            f"MR !{mr_number}: {title}",
            f"  Status : {status}",
            f"  Branch : {src} → {tgt}",
            f"  URL    : {url}",
        ]
        if description:
            lines.append(f"  Desc   : {description[:500]}{'...' if len(description) > 500 else ''}")

        changed_files = changes.get("changes", [])
        lines.append(f"\n  Files changed: {len(changed_files)}")
        if changed_files:
            lines.append("  Changed files:")
            for f in changed_files[:20]:
                fname = f.get("new_path") or f.get("old_path", "?")
                deleted = f.get("deleted_file", False)
                new_file = f.get("new_file", False)
                renamed = f.get("renamed_file", False)
                fstatus = "deleted" if deleted else ("added" if new_file else ("renamed" if renamed else "modified"))
                lines.append(f"    [{fstatus}] {fname}")
            if len(changed_files) > 20:
                lines.append(f"    ... and {len(changed_files) - 20} more files")

        return "\n".join(lines)
    except Exception as e:
        return f"[gitlab_get_mr error] {e}"


# ── Write tools (Coder) ───────────────────────────────────────

@tool(args_schema=GitlabCreateIssueArgs)
def gitlab_create_issue(
    title: str,
    body: str = "",
    labels: str = "",
    assignee: str = "",
    repo: str = "",
) -> str:
    """Create a new GitLab issue.

    Returns the URL and IID of the created issue.
    Auto-detects project from git remote if not specified.
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        payload: dict = {"title": title}
        if body:
            payload["description"] = body
        if labels:
            payload["labels"] = labels  # GitLab accepts comma-separated string
        if assignee:
            # GitLab requires user ID, but we accept username and look it up
            # For simplicity, skip assignee if it's not numeric
            if assignee.isdigit():
                payload["assignee_ids"] = [int(assignee)]
            else:
                # Try to look up user ID
                try:
                    users = _request("GET", "/users", params={"username": assignee})
                    if users:
                        payload["assignee_ids"] = [users[0]["id"]]
                except Exception:
                    pass

        result = _request("POST", f"/projects/{rid}/issues", json=payload)
        iid = result.get("iid")
        url = result.get("web_url", "")
        return f"Created issue #{iid}: {title}\n  {url}"
    except Exception as e:
        return f"[gitlab_create_issue error] {e}"


@tool(args_schema=GitlabCreateMRArgs)
def gitlab_create_mr(
    title: str,
    source_branch: str,
    target_branch: str = "main",
    description: str = "",
    draft: bool = False,
    remove_source_branch: bool = False,
    repo: str = "",
) -> str:
    """Create a new GitLab merge request.

    Returns the URL and IID of the created MR.
    Auto-detects project from git remote if not specified.
    The source branch must already exist on the remote (use git_push first).
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        mr_title = f"Draft: {title}" if draft else title
        payload: dict = {
            "title": mr_title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "remove_source_branch": remove_source_branch,
        }
        if description:
            payload["description"] = description

        result = _request("POST", f"/projects/{rid}/merge_requests", json=payload)
        iid = result.get("iid")
        url = result.get("web_url", "")
        draft_str = " [DRAFT]" if draft else ""
        return f"Created MR !{iid}{draft_str}: {title}\n  {source_branch} → {target_branch}\n  {url}"
    except Exception as e:
        return f"[gitlab_create_mr error] {e}"


@tool(args_schema=GitlabCommentArgs)
def gitlab_comment(
    number: int,
    body: str,
    resource_type: str = "issue",
    repo: str = "",
) -> str:
    """Add a note (comment) to a GitLab issue or merge request.

    Set resource_type to 'issue' (default) or 'mr' to target the right endpoint.
    Returns the URL of the project after commenting.
    """
    try:
        repo = _detect_repo(repo)
        rid = _encode_repo(repo)
        if resource_type == "mr":
            path = f"/projects/{rid}/merge_requests/{number}/notes"
        else:
            path = f"/projects/{rid}/issues/{number}/notes"

        _request("POST", path, json={"body": body})
        project_url = f"{(config.GITLAB_INSTANCE_URL or 'https://gitlab.com').rstrip('/')}/{repo}"
        type_str = f"MR !{number}" if resource_type == "mr" else f"issue #{number}"
        return f"Comment posted on {type_str} in {repo}.\n  {project_url}"
    except Exception as e:
        return f"[gitlab_comment error] {e}"
