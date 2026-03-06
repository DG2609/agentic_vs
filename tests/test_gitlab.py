"""Tests for agent/tools/gitlab.py (3A-4 GitLab integration)."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import config

from agent.tools.gitlab import (
    gitlab_list_issues,
    gitlab_list_mrs,
    gitlab_get_mr,
    gitlab_create_issue,
    gitlab_create_mr,
    gitlab_comment,
)


# ── Tool invoke helpers ────────────────────────────────────────

def _list_issues(**kwargs):
    return gitlab_list_issues.invoke({"repo": "ns/proj", **kwargs})

def _list_mrs(**kwargs):
    return gitlab_list_mrs.invoke({"repo": "ns/proj", **kwargs})

def _get_mr(**kwargs):
    defaults = {"mr_number": 1, "repo": "ns/proj"}
    return gitlab_get_mr.invoke({**defaults, **kwargs})

def _create_issue(**kwargs):
    defaults = {"title": "Bug report", "body": "", "labels": "", "assignee": "", "repo": "ns/proj"}
    return gitlab_create_issue.invoke({**defaults, **kwargs})

def _create_mr(**kwargs):
    defaults = {"title": "Fix bug", "source_branch": "fix/bug", "target_branch": "main",
                "description": "", "draft": False, "remove_source_branch": False, "repo": "ns/proj"}
    return gitlab_create_mr.invoke({**defaults, **kwargs})

def _comment(**kwargs):
    defaults = {"number": 1, "body": "LGTM", "resource_type": "issue", "repo": "ns/proj"}
    return gitlab_comment.invoke({**defaults, **kwargs})


def _mock_request(return_value):
    return patch("agent.tools.gitlab._request", return_value=return_value)

def _mock_detect(repo="ns/proj"):
    return patch("agent.tools.gitlab._detect_repo", return_value=repo)


# ── _detect_repo ──────────────────────────────────────────────

def test_detect_repo_explicit():
    """Explicit repo arg is returned as-is."""
    from agent.tools.gitlab import _detect_repo
    assert _detect_repo("my/project") == "my/project"


def test_detect_repo_ssh_gitlab_com():
    """SSH remote git@gitlab.com:ns/proj.git is parsed correctly."""
    from agent.tools.gitlab import _detect_repo
    mock = MagicMock(returncode=0, stdout="git@gitlab.com:ns/proj.git\n")
    with patch("subprocess.run", return_value=mock):
        with patch.object(config, "GITLAB_INSTANCE_URL", "https://gitlab.com"):
            assert _detect_repo("") == "ns/proj"


def test_detect_repo_https_gitlab_com():
    """HTTPS remote https://gitlab.com/ns/proj is parsed correctly."""
    from agent.tools.gitlab import _detect_repo
    mock = MagicMock(returncode=0, stdout="https://gitlab.com/ns/proj.git\n")
    with patch("subprocess.run", return_value=mock):
        with patch.object(config, "GITLAB_INSTANCE_URL", "https://gitlab.com"):
            assert _detect_repo("") == "ns/proj"


def test_detect_repo_no_remote_raises():
    """No git remote → ValueError."""
    from agent.tools.gitlab import _detect_repo
    mock = MagicMock(returncode=128, stdout="")
    with patch("subprocess.run", return_value=mock):
        with pytest.raises(ValueError, match="namespace/project"):
            _detect_repo("")


def test_detect_repo_non_gitlab_url_raises():
    """GitHub URL does not match GitLab instance → ValueError."""
    from agent.tools.gitlab import _detect_repo
    mock = MagicMock(returncode=0, stdout="https://github.com/ns/proj.git\n")
    with patch("subprocess.run", return_value=mock):
        with patch.object(config, "GITLAB_INSTANCE_URL", "https://gitlab.com"):
            with pytest.raises(ValueError):
                _detect_repo("")


# ── _headers ──────────────────────────────────────────────────

def test_headers_missing_token():
    """Missing GITLAB_TOKEN raises RuntimeError."""
    from agent.tools.gitlab import _headers
    original = config.GITLAB_TOKEN
    try:
        config.GITLAB_TOKEN = ""
        with pytest.raises(RuntimeError, match="GITLAB_TOKEN"):
            _headers()
    finally:
        config.GITLAB_TOKEN = original


def test_headers_with_token():
    """Token is included as PRIVATE-TOKEN header."""
    from agent.tools.gitlab import _headers
    original = config.GITLAB_TOKEN
    try:
        config.GITLAB_TOKEN = "glpat-testtoken"
        h = _headers()
        assert h["PRIVATE-TOKEN"] == "glpat-testtoken"
    finally:
        config.GITLAB_TOKEN = original


# ── _encode_repo ──────────────────────────────────────────────

def test_encode_repo_slash():
    """Slash in repo path is URL-encoded."""
    from agent.tools.gitlab import _encode_repo
    assert _encode_repo("ns/proj") == "ns%2Fproj"


def test_encode_repo_nested():
    """Nested namespaces are fully encoded."""
    from agent.tools.gitlab import _encode_repo
    assert _encode_repo("group/subgroup/project") == "group%2Fsubgroup%2Fproject"


# ── _request error handling ────────────────────────────────────

def test_request_raises_on_http_error():
    """Non-OK response raises RuntimeError with status code."""
    from agent.tools.gitlab import _request
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"message": "404 Project Not Found"}
    with patch("requests.request", return_value=mock_resp):
        with patch("agent.tools.gitlab._headers", return_value={}):
            with pytest.raises(RuntimeError, match="404"):
                _request("GET", "/projects/ns%2Fproj/issues")


def test_request_ok_returns_json():
    """OK response returns parsed JSON."""
    from agent.tools.gitlab import _request
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"iid": 1, "title": "Bug"}]
    with patch("requests.request", return_value=mock_resp):
        with patch("agent.tools.gitlab._headers", return_value={}):
            result = _request("GET", "/projects/ns%2Fproj/issues")
    assert result[0]["iid"] == 1


def test_request_204_returns_empty_dict():
    """204 No Content returns empty dict (not crash)."""
    from agent.tools.gitlab import _request
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 204
    with patch("requests.request", return_value=mock_resp):
        with patch("agent.tools.gitlab._headers", return_value={}):
            result = _request("DELETE", "/projects/ns%2Fproj/issues/1")
    assert result == {}


# ── gitlab_list_issues ────────────────────────────────────────

def test_list_issues_empty():
    """Empty issue list returns helpful message."""
    with _mock_detect(), _mock_request([]):
        result = _list_issues(state="opened")
    assert "No opened issues" in result


def test_list_issues_with_results():
    """Issues are listed with IID, title, and URL."""
    issues = [
        {"iid": 3, "title": "Login broken", "state": "opened",
         "labels": [{"name": "bug"}], "assignee": {"username": "alice"},
         "web_url": "https://gitlab.com/ns/proj/-/issues/3"},
    ]
    with _mock_detect(), _mock_request(issues):
        result = _list_issues(state="opened")
    assert "#3" in result
    assert "Login broken" in result
    assert "alice" in result
    assert "bug" in result


def test_list_issues_error_handled():
    """API error returns error string."""
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("boom")):
        result = _list_issues()
    assert "[gitlab_list_issues error]" in result


# ── gitlab_list_mrs ───────────────────────────────────────────

def test_list_mrs_empty():
    with _mock_detect(), _mock_request([]):
        result = _list_mrs()
    assert "No opened MRs" in result


def test_list_mrs_with_results():
    mrs = [
        {"iid": 7, "title": "Add feature", "state": "opened",
         "source_branch": "feat/x", "target_branch": "main",
         "draft": False, "work_in_progress": False,
         "web_url": "https://gitlab.com/ns/proj/-/merge_requests/7"},
    ]
    with _mock_detect(), _mock_request(mrs):
        result = _list_mrs()
    assert "!7" in result
    assert "Add feature" in result
    assert "feat/x" in result
    assert "main" in result


def test_list_mrs_draft_shown():
    """Draft MR shows [DRAFT] tag."""
    mrs = [
        {"iid": 8, "title": "WIP work", "state": "opened",
         "source_branch": "wip", "target_branch": "main",
         "draft": True, "work_in_progress": False,
         "web_url": "https://gitlab.com/ns/proj/-/merge_requests/8"},
    ]
    with _mock_detect(), _mock_request(mrs):
        result = _list_mrs()
    assert "[DRAFT]" in result


def test_list_mrs_error_handled():
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("500")):
        result = _list_mrs()
    assert "[gitlab_list_mrs error]" in result


# ── gitlab_get_mr ─────────────────────────────────────────────

def test_get_mr_ok():
    """get_mr returns MR details and changed files."""
    mock_mr = {
        "iid": 5, "title": "Add login", "state": "opened",
        "source_branch": "feat/login", "target_branch": "main",
        "draft": False, "work_in_progress": False,
        "description": "Adds login flow",
        "web_url": "https://gitlab.com/ns/proj/-/merge_requests/5",
    }
    mock_changes = {
        "changes": [
            {"new_path": "auth/login.py", "deleted_file": False, "new_file": True, "renamed_file": False},
            {"new_path": "tests/test_login.py", "deleted_file": False, "new_file": True, "renamed_file": False},
        ]
    }
    with _mock_detect():
        with patch("agent.tools.gitlab._request", side_effect=[mock_mr, mock_changes]):
            result = _get_mr(mr_number=5)

    assert "Add login" in result
    assert "feat/login" in result
    assert "auth/login.py" in result


def test_get_mr_error_handled():
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("not found")):
        result = _get_mr(mr_number=99)
    assert "[gitlab_get_mr error]" in result


# ── gitlab_create_issue ───────────────────────────────────────

def test_create_issue_ok():
    """create_issue returns IID and URL."""
    mock_result = {"iid": 12, "web_url": "https://gitlab.com/ns/proj/-/issues/12"}
    with _mock_detect(), _mock_request(mock_result):
        result = _create_issue(title="Need dark mode")
    assert "#12" in result
    assert "Need dark mode" in result


def test_create_issue_error_handled():
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("403")):
        result = _create_issue(title="Bug")
    assert "[gitlab_create_issue error]" in result


def test_create_issue_schema_empty_title():
    """Empty title raises validation error."""
    from models.tool_schemas import GitlabCreateIssueArgs
    with pytest.raises(Exception):
        GitlabCreateIssueArgs(title="")


# ── gitlab_create_mr ──────────────────────────────────────────

def test_create_mr_ok():
    """create_mr returns IID and URL."""
    mock_result = {"iid": 3, "web_url": "https://gitlab.com/ns/proj/-/merge_requests/3"}
    with _mock_detect(), _mock_request(mock_result):
        result = _create_mr(title="Fix auth", source_branch="fix/auth")
    assert "!3" in result
    assert "Fix auth" in result
    assert "fix/auth" in result


def test_create_mr_draft_prefixes_title():
    """Draft MR sends 'Draft: <title>' to API."""
    mock_result = {"iid": 4, "web_url": "https://gitlab.com/ns/proj/-/merge_requests/4"}

    captured_payload = {}

    def fake_request(method, path, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return mock_result

    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=fake_request):
        result = _create_mr(title="My feature", source_branch="feat/x", draft=True)

    assert captured_payload.get("title", "").startswith("Draft:")
    assert "[DRAFT]" in result


def test_create_mr_error_handled():
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("conflict")):
        result = _create_mr(title="T", source_branch="b")
    assert "[gitlab_create_mr error]" in result


def test_create_mr_schema_empty_branch():
    """Empty source_branch raises validation error."""
    from models.tool_schemas import GitlabCreateMRArgs
    with pytest.raises(Exception):
        GitlabCreateMRArgs(title="Fix", source_branch="")


# ── gitlab_comment ────────────────────────────────────────────

def test_comment_on_issue_ok():
    """Comment on issue returns confirmation."""
    with _mock_detect(), _mock_request({"id": 99}):
        result = _comment(number=5, body="LGTM", resource_type="issue")
    assert "issue #5" in result


def test_comment_on_mr_ok():
    """Comment on MR uses /merge_requests endpoint."""
    captured_path = []

    def fake_request(method, path, **kwargs):
        captured_path.append(path)
        return {}

    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=fake_request):
        result = _comment(number=7, body="Looks good", resource_type="mr")

    assert "merge_requests" in captured_path[0]
    assert "MR !7" in result


def test_comment_error_handled():
    with _mock_detect(), patch("agent.tools.gitlab._request", side_effect=RuntimeError("403")):
        result = _comment(number=1, body="hi")
    assert "[gitlab_comment error]" in result


def test_comment_schema_empty_body():
    from models.tool_schemas import GitlabCommentArgs
    with pytest.raises(Exception):
        GitlabCommentArgs(number=1, body="")


def test_comment_schema_invalid_resource_type():
    from models.tool_schemas import GitlabCommentArgs
    with pytest.raises(Exception):
        GitlabCommentArgs(number=1, body="hi", resource_type="pr")
