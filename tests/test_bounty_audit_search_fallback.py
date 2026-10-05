# SPDX-License-Identifier: MIT
"""Focused replay for Search-rate-limit timeline fallback."""
import requests
import pytest

from concierge import bounty_audit as audit


class FakeResponse:
    def __init__(self, status=200, *, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = dict(headers or {})
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} response", response=self
            )

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = {url: list(items) for url, items in responses.items()}
        self.calls = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        queue = self.responses.get(url)
        if not queue:
            raise AssertionError(f"unexpected GET {url} {params}")
        return queue.pop(0)


def test_secondary_search_limit_uses_paginated_timeline_without_retry():
    repo = "owner/repo"
    issue = 7
    issue_url = f"https://api.github.com/repos/{repo}/issues/{issue}"
    search_url = "https://api.github.com/search/issues"
    timeline_url = f"{issue_url}/timeline"
    comments_url = f"{issue_url}/comments"
    pr_url = f"https://api.github.com/repos/{repo}/pulls/9"

    first_page = [{"event": "labeled"} for _ in range(100)]
    first_page_link = (
        f"<{timeline_url}?page=2&per_page=100>; rel=\"next\""
    )
    cross_reference = {
        "event": "cross-referenced",
        "source": {
            "type": "issue",
            "issue": {
                "number": 9,
                "title": "Fix issue 7",
                "body": None,
                "html_url": f"https://github.com/{repo}/pull/9",
                "repository_url": f"https://api.github.com/repos/{repo}",
                "pull_request": {},
            },
        },
    }
    detail = {
        "number": 9,
        "title": "Fix issue 7",
        "body": "Closes #7",
        "html_url": f"https://github.com/{repo}/pull/9",
        "state": "open",
        "merged_at": None,
        "draft": False,
        "user": {"login": "contributor"},
        "head": {
            "ref": "fix-7",
            "sha": "abc123",
            "repo": {"full_name": "contributor/repo"},
        },
    }
    session = FakeSession(
        {
            issue_url: [
                FakeResponse(payload={"state": "open", "html_url": f"https://github.com/{repo}/issues/{issue}"})
            ],
            search_url: [
                FakeResponse(
                    403,
                    payload={"message": "You have exceeded a secondary rate limit."},
                    headers={"X-RateLimit-Remaining": "9"},
                )
            ],
            timeline_url: [
                FakeResponse(payload=first_page, headers={"Link": first_page_link}),
                FakeResponse(payload=[cross_reference]),
            ],
            pr_url: [FakeResponse(payload=detail)],
            comments_url: [FakeResponse(payload=[])],
        }
    )

    result = audit.audit_bounty(
        repo, issue, token="token", session=session, max_pages=3
    )

    assert result["linked_pr_count"] == 1
    assert result["open_pr_count"] == 1
    assert result["search_truncated"] is False
    assert [url for url, _ in session.calls].count(search_url) == 1
    timeline_calls = [params for url, params in session.calls if url == timeline_url]
    assert timeline_calls == [
        {"per_page": 100, "page": 1},
        {"per_page": 100, "page": 2},
    ]


def test_unrelated_search_403_still_fails_closed():
    repo = "owner/repo"
    issue = 7
    issue_url = f"https://api.github.com/repos/{repo}/issues/{issue}"
    search_url = "https://api.github.com/search/issues"
    timeline_url = f"{issue_url}/timeline"
    blocked = FakeResponse(
        403,
        payload={"message": "Resource not accessible by integration"},
        headers={"X-RateLimit-Remaining": "9"},
    )
    session = FakeSession(
        {
            issue_url: [
                FakeResponse(payload={"state": "open", "html_url": f"https://github.com/{repo}/issues/{issue}"})
            ],
            search_url: [blocked],
        }
    )

    with pytest.raises(audit.BountyAuditError) as captured:
        audit.audit_bounty(repo, issue, token="token", session=session)

    assert captured.value.rate_limited is False
    assert blocked.closed is True
    assert timeline_url not in [url for url, _ in session.calls]
