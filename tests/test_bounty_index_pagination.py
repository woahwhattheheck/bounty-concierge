# SPDX-License-Identifier: MIT
"""Focused pagination coverage for the GitHub bounty index."""

import requests

from concierge import bounty_index


def _issue(number):
    return {
        "number": number,
        "title": f"[Bounty: {number} RTC] Issue {number}",
        "body": "",
        "html_url": f"https://github.com/a/b/issues/{number}",
        "labels": [{"name": "bounty"}],
        "created_at": "2026-01-01T00:00:00Z",
    }


class _Response:
    status_code = 200

    def __init__(self, items=None, error=None):
        self._items = items or []
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return self._items


def test_fetch_bounties_reads_all_pages(monkeypatch):
    pages = []

    def fake_get(*args, **kwargs):
        page = kwargs["params"]["page"]
        pages.append(page)
        if page == 1:
            return _Response([_issue(number) for number in range(1, 101)])
        return _Response([_issue(101)])

    monkeypatch.setattr(requests, "get", fake_get)

    bounties = bounty_index.fetch_bounties(repos=["a/b"], token="test-token")

    assert pages == [1, 2]
    assert len(bounties) == 101
    assert bounties[0]["number"] == 1
    assert bounties[-1]["number"] == 101


def test_fetch_bounties_discards_partial_repo_after_later_page_failure(monkeypatch):
    pages = []

    def fake_get(*args, **kwargs):
        page = kwargs["params"]["page"]
        pages.append(page)
        if page == 1:
            return _Response([_issue(number) for number in range(1, 101)])
        return _Response(error=requests.RequestException("page 2 unavailable"))

    monkeypatch.setattr(requests, "get", fake_get)

    bounties = bounty_index.fetch_bounties(repos=["a/b"], token="test-token")

    assert pages == [1, 2]
    assert bounties == []
