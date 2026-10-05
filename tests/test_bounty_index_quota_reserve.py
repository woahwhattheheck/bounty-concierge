# SPDX-License-Identifier: MIT
"""Focused offline coverage for bounty-index quota headroom reservations."""

import json

import requests

from concierge import bounty_index as index
from concierge import github_cooldown


def _response(*, remaining, reset=1300, next_page=False):
    result = requests.Response()
    result.status_code = 200
    result.headers["X-RateLimit-Remaining"] = str(remaining)
    result.headers["X-RateLimit-Reset"] = str(reset)
    if next_page:
        result.headers["Link"] = (
            '<https://api.github.com/repos/example/one/issues?page=2>; rel="next"'
        )
    result._content = json.dumps([
        {
            "number": 1,
            "title": "Small fix",
            "html_url": "https://github.com/example/one/issues/1",
        }
    ]).encode()
    result._content_consumed = True
    return result


def _install_replay(monkeypatch, responses, calls):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append(url)
            return responses.pop(0)

    monkeypatch.setattr(index.requests, "Session", Session)
    monkeypatch.setattr(index, "GITHUB_TOKEN", None)
    monkeypatch.setattr(index, "time", lambda: 1000.0)
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    monkeypatch.delenv("CONCIERGE_BOUNTY_QUOTA_FLOOR", raising=False)


def test_quota_floor_reserves_and_shares_headroom_without_rate_limit(
    monkeypatch, tmp_path
):
    responses = [_response(remaining=5, next_page=True)]
    calls = []
    _install_replay(monkeypatch, responses, calls)
    path = tmp_path / "shared.sqlite"

    first = index.fetch_bounties_report(
        ("example/one", "example/two"),
        cache_dir=False,
        cooldown_path=path,
        quota_floor=5,
    )

    assert len(calls) == 1
    assert first["total_count"] == 1
    assert not first["complete"]
    assert not first["rate_limited"]
    assert first["retry_after_seconds"] is None
    assert first["quota_reserved"]
    assert first["quota_remaining"] == 5
    assert first["quota_reserve_reset_at"] == 1300
    assert first["quota_reserve_source"] == "response"
    assert [row["status"] for row in first["repositories"]] == [
        "QUOTA_RESERVED_BEFORE_NEXT_PAGE",
        "NOT_ATTEMPTED_QUOTA_RESERVE",
    ]

    follower = index.fetch_bounties_report(
        ("example/three",),
        cache_dir=False,
        cooldown_path=path,
        quota_floor=0,
    )
    assert len(calls) == 1
    assert follower["quota_reserved"]
    assert not follower["rate_limited"]
    assert follower["quota_reserve_source"] == "shared"
    assert follower["repositories"][0]["status"] == "NOT_ATTEMPTED_QUOTA_RESERVE"


def test_zero_floor_preserves_existing_pagination(monkeypatch, tmp_path):
    responses = [
        _response(remaining=5, next_page=True),
        _response(remaining=4),
    ]
    calls = []
    _install_replay(monkeypatch, responses, calls)

    result = index.fetch_bounties_report(
        ("example/one",),
        cache_dir=False,
        cooldown_path=tmp_path / "shared.sqlite",
        quota_floor=0,
    )

    assert result["complete"]
    assert not result["quota_reserved"]
    assert not result["rate_limited"]
    assert len(calls) == 2
