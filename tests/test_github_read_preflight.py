# SPDX-License-Identifier: MIT
"""Focused regressions for GitHub read/discovery admission before provider I/O."""

import pytest

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_read_preflight import GitHubReadFailure, execute_read_operation
from concierge.github_rail_availability import availability_snapshot


def _run(
    path,
    transport,
    *,
    operation="census-search",
    operation_class="search",
    target="issues:q=is%3Aopen",
    recovery_owner="worker-a",
    cooldown_scope=None,
):
    return execute_read_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation=operation,
        operation_class=operation_class,
        repo="owner/repo",
        target=target,
        transport=transport,
        recovery_owner=recovery_owner,
        cooldown_scope=cooldown_scope,
    )


def _freeze(monkeypatch, now=1000.0):
    monkeypatch.setattr(github_cooldown, "time", lambda: now)
    monkeypatch.setattr(github_rail_availability, "time", lambda: now)


def test_hot_search_defers_without_transport(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(1200.0)
    calls = []

    result = _run(path, lambda: calls.append("provider"))

    assert calls == []
    assert result == {
        "status": "READ_DEFERRED",
        "provider_called": False,
        "retry_after": 200,
        "reason": "HOT",
        "rail": "private-token",
        "actor": "actor-293",
        "operation": "census-search",
        "operation_class": "search",
        "repo": "owner/repo",
        "target": "issues:q=is%3Aopen",
    }


def test_recovery_ready_admits_one_reader_and_defers_follower(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(900.0)
    calls = []
    follower = {}
    provider_result = {"ok": True, "items": [1]}

    def transport():
        calls.append("leader")
        follower["result"] = _run(
            path,
            lambda: calls.append("follower"),
            operation="comments-read",
            operation_class="comments",
            target="issues/7/comments",
            recovery_owner="worker-b",
        )
        return provider_result

    result = _run(path, transport)

    assert result is provider_result
    assert calls == ["leader"]
    assert follower["result"]["status"] == "READ_DEFERRED"
    assert follower["result"]["provider_called"] is False
    assert follower["result"]["reason"] == "RECOVERY_PROBE_IN_FLIGHT"
    assert follower["result"]["retry_after"] == 15
    assert follower["result"]["operation"] == "comments-read"
    assert follower["result"]["operation_class"] == "comments"
    assert availability_snapshot(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        now_epoch=1000.0,
    )["availability"] == "AVAILABLE"


def test_route_scoped_search_recovery_defers_follower(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential", cooldown_scope="search")
    cooldown.extend(900.0)
    calls = []
    follower = {}

    def transport():
        calls.append("leader")
        follower["result"] = _run(
            path,
            lambda: calls.append("follower"),
            cooldown_scope="search",
            recovery_owner="worker-b",
        )
        return {"ok": True}

    assert _run(
        path, transport, cooldown_scope="search", recovery_owner="worker-a",
    ) == {"ok": True}
    assert calls == ["leader"]
    assert follower["result"]["status"] == "READ_DEFERRED"
    assert follower["result"]["provider_called"] is False
    assert follower["result"]["reason"] == "RECOVERY_PROBE_IN_FLIGHT"
    assert follower["result"]["retry_after"] == 15
    assert availability_snapshot(
        path, "credential", rail="private-token", actor="actor-293",
        cooldown_scope="search", now_epoch=1000.0,
    )["availability"] == "AVAILABLE"


@pytest.mark.parametrize(
    ("error_class", "provider_called"),
    [
        ("PERMISSION_DENIED", True),
        ("PRE_PROVIDER_SAFETY_BLOCK", False),
    ],
)
def test_non_rate_failures_leave_rate_ledger_unchanged(
    tmp_path, monkeypatch, error_class, provider_called,
):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    cooldown.reserve_quota_until(900.0)
    before = cooldown.deadlines()

    def transport():
        raise GitHubReadFailure(error_class)

    result = _run(
        path,
        transport,
        operation="branch-head-read",
        operation_class="branch-head",
        target="git/ref/heads/main",
    )

    assert result["status"] == "READ_FAILED"
    assert result["reason"] == error_class
    assert result["provider_called"] is provider_called
    assert cooldown.deadlines() == before


def test_rate_limit_records_authoritative_reset_and_defers_subsequent_read(
    tmp_path, monkeypatch,
):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    cooldown.reserve_quota_until(900.0)

    def limited():
        raise GitHubReadFailure(
            "PRIMARY_RATE_LIMIT",
            retry_after_seconds=20,
            reset_at=1300,
        )

    first = _run(
        path,
        limited,
        operation="issue-read-44",
        operation_class="issue-pr-read",
        target="issues/44",
    )

    assert first == {
        "status": "READ_DEFERRED",
        "provider_called": True,
        "retry_after": 300,
        "reason": "PRIMARY_RATE_LIMIT",
        "rail": "private-token",
        "actor": "actor-293",
        "operation": "issue-read-44",
        "operation_class": "issue-pr-read",
        "repo": "owner/repo",
        "target": "issues/44",
    }
    assert cooldown.deadline() == 1300.0

    calls = []
    second = _run(
        path,
        lambda: calls.append("provider"),
        operation="issue-read-44-replay",
        operation_class="issue-pr-read",
        target="issues/44",
    )

    assert calls == []
    assert second["status"] == "READ_DEFERRED"
    assert second["provider_called"] is False
    assert second["retry_after"] == 300
    assert second["operation"] == "issue-read-44-replay"
    assert second["target"] == "issues/44"
