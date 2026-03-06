"""Tests for agent/tools/github.py."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import config


# ── _detect_repo ──────────────────────────────────────────────

def test_detect_repo_explicit():
    """Explicit repo arg is returned as-is."""
    from agent.tools.github import _detect_repo
    assert _detect_repo("owner/repo") == "owner/repo"


def test_detect_repo_ssh_url():
    """SSH remote URL is parsed correctly."""
    from agent.tools.github import _detect_repo
    mock = MagicMock(returncode=0, stdout="git@github.com:owner/myrepo.git\n")
    with patch("subprocess.run", return_value=mock):
        assert _detect_repo("") == "owner/myrepo"


def test_detect_repo_https_url():
    """HTTPS remote URL is parsed correctly."""
    from agent.tools.github import _detect_repo
    mock = MagicMock(returncode=0, stdout="https://github.com/alice/project.git\n")
    with patch("subprocess.run", return_value=mock):
        assert _detect_repo("") == "alice/project"


def test_detect_repo_no_remote_raises():
    """No git remote → ValueError with helpful message."""
    from agent.tools.github import _detect_repo
    mock = MagicMock(returncode=128, stdout="")
    with patch("subprocess.run", return_value=mock):
        with pytest.raises(ValueError, match="owner/repo"):
            _detect_repo("")


def test_detect_repo_non_github_url_raises():
    """Non-GitHub remote raises ValueError."""
    from agent.tools.github import _detect_repo
    mock = MagicMock(returncode=0, stdout="https://gitlab.com/owner/repo.git\n")
    with patch("subprocess.run", return_value=mock):
        with pytest.raises(ValueError):
            _detect_repo("")


# ── _headers ──────────────────────────────────────────────────

def test_headers_missing_token():
    """Missing GITHUB_TOKEN raises RuntimeError."""
    from agent.tools.github import _headers
    original = config.GITHUB_TOKEN
    try:
        config.GITHUB_TOKEN = ""
        with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
            _headers()
    finally:
        config.GITHUB_TOKEN = original


def test_headers_with_token():
    """Token is included correctly in headers."""
    from agent.tools.github import _headers
    original = config.GITHUB_TOKEN
    try:
        config.GITHUB_TOKEN = "ghp_testtoken"
        h = _headers()
        assert "Bearer ghp_testtoken" in h["Authorization"]
        assert "vnd.github" in h["Accept"]
    finally:
        config.GITHUB_TOKEN = original


# ── _request error handling ────────────────────────────────────

def test_request_raises_on_http_error():
    """Non-OK response raises RuntimeError with status code."""
    from agent.tools.github import _request
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"message": "Not Found"}
    with patch("requests.request", return_value=mock_resp):
        with patch("agent.tools.github._headers", return_value={}):
            with pytest.raises(RuntimeError, match="404"):
                _request("GET", "/repos/x/y")


def test_request_ok_returns_json():
    """OK response returns parsed JSON."""
    from agent.tools.github import _request
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"number": 1, "title": "Bug"}]
    with patch("requests.request", return_value=mock_resp):
        with patch("agent.tools.github._headers", return_value={}):
            result = _request("GET", "/repos/x/y/issues")
    assert result[0]["number"] == 1


# ── Tool: github_list_issues ───────────────────────────────────

def _mock_request(return_value):
    return patch("agent.tools.github._request", return_value=return_value)


def _mock_detect(repo="owner/repo"):
    return patch("agent.tools.github._detect_repo", return_value=repo)


def test_github_list_issues_no_issues():
    """Empty issue list returns helpful message."""
    with _mock_detect(), _mock_request([]):
        result = github_list_issues_invoke(state="open")
    assert "No open issues" in result


def test_github_list_issues_filters_prs():
    """Issues with pull_request key are excluded."""
    issues = [
        {"number": 1, "title": "Real issue", "state": "open",
         "labels": [], "assignee": None, "html_url": "https://github.com/x/y/issues/1"},
        {"number": 2, "title": "A PR", "state": "open",
         "pull_request": {}, "labels": [], "assignee": None, "html_url": ""},
    ]
    with _mock_detect(), _mock_request(issues):
        result = github_list_issues_invoke(state="open")
    assert "#1" in result
    assert "#2" not in result


def test_github_list_issues_error_handled():
    """API error returns error string, not exception."""
    with _mock_detect(), patch("agent.tools.github._request", side_effect=RuntimeError("boom")):
        result = github_list_issues_invoke(state="open")
    assert "[github_list_issues error]" in result


# ── Tool: github_create_pr ────────────────────────────────────

def test_github_create_pr_ok():
    """create_pr returns PR number and URL."""
    mock_result = {"number": 42, "html_url": "https://github.com/x/y/pull/42"}
    with _mock_detect(), _mock_request(mock_result):
        result = github_create_pr_invoke(title="Fix bug", branch="fix/bug")
    assert "#42" in result
    assert "Fix bug" in result


def test_github_create_pr_error():
    """API error is caught and returned as string."""
    with _mock_detect(), patch("agent.tools.github._request", side_effect=RuntimeError("no auth")):
        result = github_create_pr_invoke(title="Fix", branch="feat")
    assert "[github_create_pr error]" in result


# ── Tool: github_comment ──────────────────────────────────────

def test_github_comment_ok():
    """Comment returns URL."""
    mock_result = {"html_url": "https://github.com/x/y/issues/5#issuecomment-1"}
    with _mock_detect(), _mock_request(mock_result):
        result = github_comment_invoke(number=5, body="LGTM!")
    assert "#5" in result
    assert "github.com" in result


def test_github_comment_error():
    with _mock_detect(), patch("agent.tools.github._request", side_effect=RuntimeError("forbidden")):
        result = github_comment_invoke(number=1, body="hi")
    assert "[github_comment error]" in result


# ── Tool: github_get_pr ────────────────────────────────────────

def test_github_get_pr_ok():
    """get_pr returns PR details including changed files."""
    mock_pr = {
        "number": 10, "title": "Add feature", "state": "open",
        "merged": False, "draft": False,
        "head": {"ref": "feat/new"}, "base": {"ref": "main"},
        "html_url": "https://github.com/x/y/pull/10",
        "body": "Description here",
        "additions": 100, "deletions": 20, "changed_files": 3,
    }
    mock_files = [
        {"filename": "src/main.py", "status": "modified", "additions": 50, "deletions": 10},
    ]
    with _mock_detect():
        with patch("agent.tools.github._request", side_effect=[mock_pr, mock_files]):
            result = github_get_pr_invoke(pr_number=10)
    assert "Add feature" in result
    assert "feat/new" in result
    assert "src/main.py" in result


# ── Schemas ────────────────────────────────────────────────────

def test_github_create_pr_schema_empty_title():
    from models.tool_schemas import GithubCreatePRArgs
    with pytest.raises(Exception):
        GithubCreatePRArgs(title="", branch="feat")


def test_github_create_pr_schema_empty_branch():
    from models.tool_schemas import GithubCreatePRArgs
    with pytest.raises(Exception):
        GithubCreatePRArgs(title="Fix", branch="")


def test_github_comment_schema_empty_body():
    from models.tool_schemas import GithubCommentArgs
    with pytest.raises(Exception):
        GithubCommentArgs(number=1, body="")


# ── Helper to invoke tools via .invoke() ──────────────────────

from agent.tools.github import (
    github_list_issues,
    github_create_pr,
    github_comment,
    github_get_pr,
)


def github_list_issues_invoke(**kwargs):
    return github_list_issues.invoke({"repo": "", **kwargs})


def github_create_pr_invoke(**kwargs):
    defaults = {"title": "T", "branch": "b", "base": "main", "body": "", "draft": False, "repo": ""}
    return github_create_pr.invoke({**defaults, **kwargs})


def github_comment_invoke(**kwargs):
    defaults = {"number": 1, "body": "x", "repo": ""}
    return github_comment.invoke({**defaults, **kwargs})


def github_get_pr_invoke(**kwargs):
    defaults = {"pr_number": 1, "repo": ""}
    return github_get_pr.invoke({**defaults, **kwargs})
