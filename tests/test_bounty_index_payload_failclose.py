# SPDX-License-Identifier: MIT
"""Focused fail-closed tests for bounty API payload decoding."""

import requests

from concierge import bounty_index


class _Response:
    status_code = 200
    links = {}

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        return None

    def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


def test_fetch_bounties_skips_repo_with_invalid_json(monkeypatch, capsys):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(error=ValueError("bad json")))

    assert bounty_index.fetch_bounties(repos=["example/repo"], token="token") == []
    assert "failed to decode example/repo" in capsys.readouterr().err


def test_fetch_bounties_skips_repo_with_non_list_payload(monkeypatch, capsys):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(payload={"message": "unexpected"}))

    assert bounty_index.fetch_bounties(repos=["example/repo"], token="token") == []
    assert "unsupported payload for example/repo: expected list" in capsys.readouterr().err


def test_fetch_bounties_ignores_non_object_rows_but_keeps_valid_issues(monkeypatch):
    payload = [
        None,
        "unexpected",
        {
            "number": 7,
            "title": "[Bounty: 25 RTC] Valid issue",
            "body": "",
            "html_url": "https://github.com/example/repo/issues/7",
            "labels": [{"name": "bounty"}],
            "created_at": "2026-01-01T00:00:00Z",
        },
    ]
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(payload=payload))

    result = bounty_index.fetch_bounties(repos=["example/repo"], token="token")
    assert [item["number"] for item in result] == [7]
    assert result[0]["reward_rtc"] == 25.0


def test_fetch_bounties_ignores_malformed_object_rows_but_keeps_valid_sibling(monkeypatch):
    common = {
        "title": "Malformed bounty",
        "body": "",
        "html_url": "https://github.com/example/repo/issues/1",
        "labels": [{"name": "bounty"}],
        "created_at": "2026-01-01T00:00:00Z",
    }
    payload = [
        {**common, "number": 1, "labels": [None]},
        {key: value for key, value in common.items()},
        {**common, "number": 3, "html_url": None},
        {**common, "number": 4, "title": {"structured": True}},
        {**common, "number": 5, "body": {"structured": True}},
        {**common, "number": 6, "labels": {"name": "bounty"}},
        {**common, "number": 7, "labels": [{"name": 7}]},
        {**common, "number": 8, "created_at": {"structured": True}},
        {
            "number": 11,
            "title": "[Bounty: 40 RTC] Valid sibling",
            "body": "Python fix",
            "html_url": "https://github.com/example/repo/issues/11",
            "labels": [{"name": "bounty"}],
            "created_at": "2026-01-02T00:00:00Z",
        },
    ]
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _Response(payload=payload))

    result = bounty_index.fetch_bounties(repos=["example/repo"], token="token")

    assert [item["number"] for item in result] == [11]
    assert result[0]["reward_rtc"] == 40.0
    assert result[0]["skills"] == ["python"]


def test_fetch_bounties_continues_to_later_repo_after_malformed_row(monkeypatch):
    malformed = {
        "number": 1,
        "title": {"structured": True},
        "body": "",
        "html_url": "https://github.com/bad/repo/issues/1",
        "labels": [{"name": "bounty"}],
    }
    valid = {
        "number": 12,
        "title": "[Bounty: 60 RTC] Later repository",
        "body": "Rust fix",
        "html_url": "https://github.com/good/repo/issues/12",
        "labels": [{"name": "bounty"}],
        "created_at": "2026-01-03T00:00:00Z",
    }

    def fake_get(url, **kwargs):
        if "/bad/repo/" in url:
            return _Response(payload=[malformed])
        if "/good/repo/" in url:
            return _Response(payload=[valid])
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    result = bounty_index.fetch_bounties(
        repos=["bad/repo", "good/repo"],
        token="token",
    )

    assert [(item["repo"], item["number"]) for item in result] == [("good/repo", 12)]
    assert result[0]["reward_rtc"] == 60.0
    assert result[0]["skills"] == ["rust"]
