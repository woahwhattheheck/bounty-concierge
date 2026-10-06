# SPDX-License-Identifier: MIT
"""Focused admission checks for high-fanout GitHub read/discovery calls."""

import pytest

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_read_preflight import GitHubReadResult, execute_read_operation
from concierge.github_rail_availability import availability_snapshot


def _run(
    path,
    transport,
    *,
    operation="search-1",
    operation_class="search",
    resource="repo:q",
    owner="worker-a",
):
    return execute_read_operation(
        path,
        "credential",
        rail="managed-app",
        actor="actor-293",
        operation=operation,
        operation_class=operation_class,
        resource=resource,
        transport=transport,
        recovery_owner=owner,
    )


def _freeze(monkeypatch, value=1000.0):
    monkeypatch.setattr(github_cooldown, "time", lambda: value)
    monkeypatch.setattr(github_rail_availability, "time", lambda: value)


def test_hot_search_defers_without_provider_io_and_preserves_metadata(
    tmp_path, monkeypatch,
):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(1200.0)
    calls = []

    result = _run(
        path,
        lambda: calls.append("provider"),
        operation="search-open-prs",
        operation_class="search",
        resource="owner/repo:open-prs",
    )

    assert calls == []
    assert result == {
        "status": "READ_DEFERRED",
        "provider_called": False,
        "reason": "HOT",
        "retry_after": 200,
        "rail": "managed-app",
        "actor": "actor-293",
        "operation": "search-open-prs",
        "operation_class": "search",
        "resource": "owner/repo:open-prs",
    }


def test_recovery_ready_admits_one_probe_and_defers_follower(
    tmp_path, monkeypatch,
):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(900.0)
    calls = []
    follower = {}

    def transport():
        calls.append("leader")
        follower["result"] = _run(
            path,
            lambda: calls.append("follower"),
            operation="read-follower",
            operation_class="head",
            resource="owner/repo@main",
            owner="worker-b",
        )
        return GitHubReadResult({"ok": True})

    result = _run(
        path,
        transport,
        operation="read-leader",
        operation_class="head",
        resource="owner/repo@main",
    )

    assert isinstance(result, GitHubReadResult)
    assert calls == ["leader"]
    assert follower["result"]["status"] == "READ_DEFERRED"
    assert follower["result"]["reason"] == "RECOVERY_PROBE_IN_FLIGHT"
    assert follower["result"]["provider_called"] is False
    assert availability_snapshot(
        path,
        "credential",
        rail="managed-app",
        actor="actor-293",
        now_epoch=1000.0,
    )["availability"] == "AVAILABLE"


def test_permission_403_does_not_create_rate_cooldown(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    provider = GitHubReadResult(
        {"message": "Resource not accessible by integration"},
        status_code=403,
    )

    result = _run(path, lambda: provider)

    assert result is provider
    assert not path.exists()


def test_transport_exception_does_not_create_rate_cooldown(tmp_path):
    path = tmp_path / "cooldown.sqlite"

    def blocked():
        raise RuntimeError("transport stopped before provider I/O")

    with pytest.raises(RuntimeError, match="stopped before provider"):
        _run(path, blocked)

    assert not path.exists()


def test_rate_limit_receipt_records_authoritative_deadline_and_defers_next_read(
    tmp_path, monkeypatch,
):
    _freeze(monkeypatch)
    path = tmp_path / "cooldown.sqlite"
    calls = []
    limited = GitHubReadResult(
        {"message": "API rate limit exceeded"},
        status_code=403,
        headers={
            "Retry-After": "20",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1300",
        },
    )

    first = _run(
        path,
        lambda: calls.append("first") or limited,
        operation="issue-census",
        operation_class="search",
        resource="owner/repo:issues",
    )
    second = _run(
        path,
        lambda: calls.append("second") or GitHubReadResult({"ok": True}),
        operation="issue-census-retry",
        operation_class="search",
        resource="owner/repo:issues",
    )

    assert calls == ["first"]
    assert first["status"] == "READ_DEFERRED"
    assert first["provider_called"] is True
    assert first["reason"] == "PRIMARY_RATE_LIMIT"
    assert first["retry_after"] == 300
    assert first["operation"] == "issue-census"
    assert first["operation_class"] == "search"
    assert first["resource"] == "owner/repo:issues"
    assert second["status"] == "READ_DEFERRED"
    assert second["provider_called"] is False
    assert second["retry_after"] == 300
