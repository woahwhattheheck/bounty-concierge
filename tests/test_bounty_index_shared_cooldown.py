# SPDX-License-Identifier: MIT
"""Offline HTTP replay with the real shared SQLite cooldown implementation."""
import json
from unittest.mock import Mock

import pytest
import requests

from concierge import bounty_index as index
from concierge import github_cooldown


def response(status=200, *, headers=None, payload=None, next_page=False):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    if next_page:
        result.headers["Link"] = '<https://api.github.com/repos/example/one/issues?page=2>; rel="next"'
    result._content = json.dumps([] if payload is None else payload).encode()
    result._content_consumed = True
    result.close = Mock(wraps=result.close)
    return result


@pytest.fixture
def replay(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr(index, "time", lambda: clock[0])
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    monkeypatch.setattr(index, "GITHUB_TOKEN", None)
    monkeypatch.delenv("CONCIERGE_BOUNTY_COOLDOWN", raising=False)
    responses = []
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            calls.append(url)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(index.requests, "Session", Session)
    path = tmp_path / "quota.sqlite"

    def fetch(repos=("example/one",), **kwargs):
        return index.fetch_bounties_report(
            repos, "test-token", cache_dir=False, cooldown_path=path, **kwargs,
        )

    return fetch, responses, calls, path, clock


def test_secondary_throttle_defers_second_worker_then_escalates(replay):
    fetch, responses, calls, path, clock = replay
    limited = response(403, headers={"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "9000"},
                       payload={"message": "You have exceeded a secondary rate limit."})
    responses.append(limited)
    assert fetch()["repositories"][0]["status"] == "RATE_LIMITED"
    follower = fetch()
    assert len(calls) == 1
    assert follower["repositories"][0]["status"] == "NOT_ATTEMPTED_SHARED_COOLDOWN"
    assert follower["retry_after_seconds"] == 60
    assert not follower["complete"]
    limited.close.assert_called_once()
    clock[0] = 1060.0
    responses.append(response(429))
    fetch()
    assert len(calls) == 2
    assert github_cooldown.GitHubCooldown(path, "test-token").deadline() == 1180.0


def test_response_hook_and_provider_deadlines_are_shared(replay):
    fetch, responses, calls, path, clock = replay
    limited = response(429, headers={"Retry-After": "90"})
    responses.append(requests.HTTPError(response=limited))
    first = fetch()
    assert first["repositories"][0]["http_status"] == 429
    assert fetch()["retry_after_seconds"] == 90
    assert len(calls) == 1
    limited.close.assert_called_once()
    clock[0] = 1090.0
    responses.append(response(403, headers={"Retry-After": "0"}))
    fetch()
    responses.append(response())
    assert fetch()["complete"]
    assert len(calls) == 3


def test_successful_last_quota_keeps_rows_and_defers_other_worker(replay, monkeypatch):
    fetch, responses, calls, path, clock = replay
    monkeypatch.setattr(index, "extract_reward_evidence", lambda *args: {"amount_rtc": 1})
    row = {"number": 1, "title": "Python fix", "html_url": "https://github.com/example/one/issues/1"}
    responses.append(response(headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1300"},
                              payload=[row]))
    first = fetch()
    assert first["complete"] and first["total_count"] == 1
    assert first["rate_limited"]
    assert fetch()["retry_after_seconds"] == 300
    assert len(calls) == 1


def test_sibling_deadline_between_pages_retains_partial_rows(replay, monkeypatch):
    fetch, responses, calls, path, clock = replay
    store = github_cooldown.GitHubCooldown(path, "test-token")

    def reward(*args):
        store.extend(1120.0)
        return {"amount_rtc": 1}

    monkeypatch.setattr(index, "extract_reward_evidence", reward)
    row = {"number": 1, "title": "Python fix", "html_url": "https://github.com/example/one/issues/1"}
    responses.append(response(payload=[row], next_page=True))
    result = fetch(("example/one", "example/two"))
    assert len(calls) == 1 and result["total_count"] == 1
    assert [item["status"] for item in result["repositories"]] == [
        "RATE_LIMITED_BEFORE_NEXT_PAGE", "NOT_ATTEMPTED_RATE_LIMIT",
    ]
    assert not result["complete"]


def test_store_errors_do_not_bypass_or_erase_responses(replay, monkeypatch):
    fetch, responses, calls, path, clock = replay
    path.write_text("not a sqlite database")
    result = fetch(("example/one", "example/two"))
    assert not calls and not result["complete"]
    assert [item["status"] for item in result["repositories"]] == [
        "COOLDOWN_STATE_ERROR", "NOT_ATTEMPTED_COOLDOWN_ERROR",
    ]
    path.unlink()

    def fail_write(*args):
        raise github_cooldown.CooldownStateError("private diagnostic")

    monkeypatch.setattr(github_cooldown.GitHubCooldown, "extend", fail_write)
    final_page = response(headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1300"})
    responses.append(final_page)
    result = fetch(("example/one", "example/two"))
    assert len(calls) == 1 and not result["complete"]
    assert result["repositories"][0]["http_status"] == 200
    assert result["repositories"][0]["status"] == "COOLDOWN_STATE_ERROR"
    assert "private diagnostic" not in json.dumps(result)
    final_page.close.assert_called_once()


def test_ordinary_403_and_explicit_opt_out_preserve_legacy_behavior(replay, monkeypatch):
    fetch, responses, calls, path, clock = replay
    responses.extend([response(403, payload={"message": "Resource not accessible by integration"}), response()])
    result = fetch(("example/one", "example/two"))
    assert len(calls) == 2
    assert not result["rate_limited"]
    assert github_cooldown.GitHubCooldown(path, "test-token").deadline() is None
    github_cooldown.GitHubCooldown(path, "test-token").extend(1200.0)
    monkeypatch.setenv("CONCIERGE_BOUNTY_COOLDOWN", str(path))
    with pytest.raises(index.BountyFetchIncompleteError):
        index.aggregate(["example/one"], "test-token", cache_dir=False)
    assert len(calls) == 2
    responses.append(response())
    assert index.fetch_bounties(["example/one"], "test-token", cache_dir=False, cooldown_path=False) == []
    assert len(calls) == 3
