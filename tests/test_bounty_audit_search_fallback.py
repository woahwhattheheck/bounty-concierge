# SPDX-License-Identifier: MIT
"""Focused replay for Search throttling in the canonical bounty audit."""
import json
from time import time

import pytest
import requests

from concierge import bounty_audit
from concierge.github_cooldown import GitHubCooldown


def response(status=200, *, headers=None, payload=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


class ReplaySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.responses.pop(0)


def test_search_rate_limit_falls_back_to_cross_reference_timeline_without_retry():
    issue = {"number": 1, "state": "open", "html_url": "https://github.com/example/project/issues/1"}
    limited = response(
        403,
        headers={"Retry-After": "60", "X-RateLimit-Remaining": "10"},
        payload={"message": "You have exceeded a secondary rate limit."},
    )
    timeline = [{
        "event": "cross-referenced",
        "source": {"issue": {
            "number": 9,
            "title": "Fix retry path",
            "body": "Retry persistence follow-up",
            "html_url": "https://github.com/example/project/pull/9",
            "repository_url": "https://api.github.com/repos/example/project",
            "pull_request": {"url": "https://api.github.com/repos/example/project/pulls/9"},
        }},
    }]
    detail = {
        "number": 9,
        "title": "Fix retry path",
        "body": "Retry persistence follow-up",
        "html_url": "https://github.com/example/project/pull/9",
        "state": "open",
        "draft": False,
        "merged_at": None,
        "user": {"login": "builder"},
        "head": {"ref": "fix-1", "sha": "abc", "repo": {"full_name": "builder/project"}},
    }
    session = ReplaySession([
        response(payload=issue),
        limited,
        response(payload=timeline),
        response(payload=detail),
        response(payload=[]),
    ])

    audit = bounty_audit.audit_bounty("example/project", 1, session=session, max_pages=3)

    assert audit["linked_pr_count"] == 1
    assert audit["open_pr_count"] == 1
    assert audit["search_truncated"] is False
    assert sum(url == "https://api.github.com/search/issues" for url in session.calls) == 1
    assert "https://api.github.com/repos/example/project/issues/1/timeline" in session.calls


def test_ordinary_search_permission_403_remains_fatal():
    session = ReplaySession([
        response(payload={"number": 1, "state": "open", "html_url": "https://github.com/example/project/issues/1"}),
        response(403, payload={"message": "Resource not accessible by integration"}),
    ])

    with pytest.raises(bounty_audit.BountyAuditError):
        bounty_audit.audit_bounty("example/project", 1, session=session, max_pages=3)

    assert session.calls == [
        "https://api.github.com/repos/example/project/issues/1",
        "https://api.github.com/search/issues",
    ]

def test_search_rate_limit_records_only_search_provider_cooldown(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.sqlite"
    monkeypatch.setenv("CONCIERGE_BOUNTY_COOLDOWN", str(path))
    issue = {"number": 1, "state": "open", "html_url": "https://github.com/example/project/issues/1"}
    limited = response(
        403,
        headers={"Retry-After": "60", "X-RateLimit-Remaining": "10"},
        payload={"message": "You have exceeded a secondary rate limit."},
    )
    session = ReplaySession([
        response(payload=issue),
        limited,
        response(payload=[]),
        response(payload=[]),
    ])

    bounty_audit.audit_bounty(
        "example/project", 1, token="test-token", session=session, max_pages=3
    )

    search = GitHubCooldown(path, "test-token", cooldown_scope="search")
    core = GitHubCooldown(path, "test-token", cooldown_scope="core")
    legacy = GitHubCooldown(path, "test-token")
    assert search.deadline() is not None and search.deadline() > time()
    assert core.deadline() is None
    assert legacy.deadline() is None


def test_active_search_cooldown_skips_search_and_uses_timeline(tmp_path, monkeypatch):
    path = tmp_path / "cooldown.sqlite"
    monkeypatch.setenv("CONCIERGE_BOUNTY_COOLDOWN", str(path))
    GitHubCooldown(path, "test-token", cooldown_scope="search").extend(time() + 300)
    issue = {"number": 1, "state": "open", "html_url": "https://github.com/example/project/issues/1"}
    session = ReplaySession([
        response(payload=issue),
        response(payload=[]),
        response(payload=[]),
    ])

    audit = bounty_audit.audit_bounty(
        "example/project", 1, token="test-token", session=session, max_pages=3
    )

    assert audit["linked_pr_count"] == 0
    assert "https://api.github.com/search/issues" not in session.calls
    assert "https://api.github.com/repos/example/project/issues/1/timeline" in session.calls

