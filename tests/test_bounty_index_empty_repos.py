# SPDX-License-Identifier: MIT
"""Explicit repository override coverage for the bounty index."""

from concierge import bounty_index


def test_explicit_empty_repos_performs_no_requests(monkeypatch):
    monkeypatch.setattr(bounty_index, "REPOS", ["configured/repo"])
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("explicit empty repository list must not fall back")

    monkeypatch.setattr(bounty_index.requests, "get", fake_get)

    assert bounty_index.fetch_bounties(repos=[], token="unit-test") == []
    assert calls == []


def test_none_repos_uses_configured_default(monkeypatch):
    monkeypatch.setattr(bounty_index, "REPOS", ["configured/repo"])
    seen = []

    class Response:
        status_code = 200
        links = {}

        def raise_for_status(self):
            return None

        def json(self):
            return []

    def fake_get(url, **kwargs):
        seen.append(url)
        return Response()

    monkeypatch.setattr(bounty_index.requests, "get", fake_get)

    assert bounty_index.fetch_bounties(repos=None, token="unit-test") == []
    assert seen == ["https://api.github.com/repos/configured/repo/issues"]
