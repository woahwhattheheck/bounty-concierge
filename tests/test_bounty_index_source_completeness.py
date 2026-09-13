# SPDX-License-Identifier: MIT
"""Authoritative bounty-index completeness and atomic-publication regressions."""

import json
from pathlib import Path

import pytest
import requests

from concierge import bounty_index


class _Response:
    def __init__(self, payload=None, *, status=200, error=None, next_page=False):
        self.status_code = status
        self._payload = payload
        self._error = error
        self.links = {"next": {"url": "https://example.invalid/page/2"}} if next_page else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


def _issue(number=1):
    return {
        "number": number,
        "title": f"[Bounty: {number * 10} RTC] Task {number}",
        "body": "Python fix",
        "html_url": f"https://github.com/example/repo/issues/{number}",
        "labels": [{"name": "bounty"}],
        "created_at": "2026-09-13T00:00:00Z",
    }


def test_aggregate_treats_explicit_empty_sources_as_complete(monkeypatch):
    monkeypatch.setattr(
        bounty_index.requests,
        "get",
        lambda *args, **kwargs: pytest.fail("repos=[] must not perform network I/O"),
    )

    result = bounty_index.aggregate(repos=[], token="unit-test")

    assert result["total_count"] == 0
    assert result["bounties"] == []


@pytest.mark.parametrize(
    "response",
    [
        _Response([], status=404),
        _Response(error=ValueError("bad json")),
        _Response({"message": "unexpected"}),
        _Response([{"number": 1, "title": {"structured": True}}]),
    ],
    ids=["configured-source-404", "invalid-json", "non-list-payload", "malformed-row"],
)
def test_aggregate_fails_closed_on_incomplete_source(monkeypatch, response):
    monkeypatch.setattr(bounty_index.requests, "get", lambda *args, **kwargs: response)

    with pytest.raises(bounty_index.BountyIndexIncompleteError):
        bounty_index.aggregate(repos=["example/repo"], token="unit-test")


def test_aggregate_fails_closed_when_later_page_times_out(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"]["page"])
        if kwargs["params"]["page"] == 1:
            return _Response([_issue(1)], next_page=True)
        raise requests.Timeout("page two timed out")

    monkeypatch.setattr(bounty_index.requests, "get", get)

    with pytest.raises(bounty_index.BountyIndexIncompleteError, match="page 2"):
        bounty_index.aggregate(repos=["example/repo"], token="unit-test")

    assert calls == [1, 2]


def test_best_effort_fetch_remains_salvage_mode(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("/bad/repo/issues"):
            raise requests.Timeout("bad repo unavailable")
        return _Response([_issue(2)])

    monkeypatch.setattr(bounty_index.requests, "get", get)

    result = bounty_index.fetch_bounties(
        repos=["bad/repo", "good/repo"], token="unit-test"
    )

    assert [(row["repo"], row["number"]) for row in result] == [("good/repo", 2)]


def test_atomic_write_preserves_previous_index_on_source_failure(monkeypatch, tmp_path):
    target = tmp_path / "bounty_index.json"
    old = b'{"sentinel":"last-known-good"}\n'
    target.write_bytes(old)

    monkeypatch.setattr(
        bounty_index.requests,
        "get",
        lambda *args, **kwargs: _Response(error=ValueError("truncated response")),
    )

    with pytest.raises(bounty_index.BountyIndexIncompleteError):
        bounty_index.write_index_atomic(
            target, repos=["example/repo"], token="unit-test"
        )

    assert target.read_bytes() == old
    assert list(tmp_path.glob(".bounty_index.json.*.tmp")) == []


def test_atomic_write_replaces_target_only_after_complete_success(monkeypatch, tmp_path):
    target = tmp_path / "bounty_index.json"
    target.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(
        bounty_index.requests,
        "get",
        lambda *args, **kwargs: _Response([_issue(3)]),
    )

    data = bounty_index.write_index_atomic(
        target, repos=["example/repo"], token="unit-test"
    )
    published = json.loads(target.read_text(encoding="utf-8"))

    assert data["total_count"] == 1
    assert published["total_count"] == 1
    assert published["bounties"][0]["number"] == 3
    assert list(tmp_path.glob(".bounty_index.json.*.tmp")) == []


def test_cli_output_failure_returns_nonzero_without_clobber(monkeypatch, tmp_path):
    target = tmp_path / "bounty_index.json"
    target.write_text("last-good\n", encoding="utf-8")
    monkeypatch.setattr(
        bounty_index,
        "aggregate",
        lambda repos=None, token=None: (_ for _ in ()).throw(
            bounty_index.BountyIndexIncompleteError("source incomplete")
        ),
    )

    assert bounty_index.main(["--output", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "last-good\n"


def test_sync_workflow_uses_atomic_output_path():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "bounty_index_sync.yml"
    ).read_text(encoding="utf-8")

    assert "python -m concierge.bounty_index --output data/bounty_index.json" in workflow
    assert "python -m concierge.bounty_index > data/bounty_index.json" not in workflow
