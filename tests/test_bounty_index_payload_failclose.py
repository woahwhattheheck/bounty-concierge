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
