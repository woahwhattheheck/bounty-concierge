# SPDX-License-Identifier: MIT
"""Focused tests for GitHub publication admission before provider I/O."""

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_publish_preflight import execute_publish_operation
from concierge.github_rail_availability import availability_snapshot


HEAD = "a" * 40


def _run(path, transport, *, operation, recovery_owner):
    return execute_publish_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation=operation,
        action="update-pr-body",
        repo="owner/repo",
        carrier="owner/repo#123",
        expected_head=HEAD,
        transport=transport,
        recovery_owner=recovery_owner,
    )


def test_hot_rail_defers_without_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    monkeypatch.setattr(github_rail_availability, "time", lambda: 1000.0)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(1200.0)
    calls = []

    result = _run(
        path,
        lambda: calls.append("provider"),
        operation="publish-123",
        recovery_owner="worker-a",
    )

    assert calls == []
    assert result == {
        "status": "RAIL_DEFERRED",
        "provider_called": False,
        "retry_after": 200,
        "operation": "publish-123",
        "action": "update-pr-body",
        "repo": "owner/repo",
        "carrier": "owner/repo#123",
        "expected_head": HEAD,
    }


def test_recovery_ready_admits_one_operation_and_defers_follower(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    monkeypatch.setattr(github_rail_availability, "time", lambda: 1000.0)
    path = tmp_path / "cooldown.sqlite"
    GitHubCooldown(path, "credential").extend(900.0)
    calls = []
    follower = {}
    provider_collision = {
        "status": "PROVIDER_COLLISION",
        "expected_head": HEAD,
        "observed_head": "b" * 40,
    }

    def transport():
        calls.append("leader")
        follower["result"] = _run(
            path,
            lambda: calls.append("follower"),
            operation="publish-follower",
            recovery_owner="worker-b",
        )
        return provider_collision

    result = _run(
        path,
        transport,
        operation="publish-leader",
        recovery_owner="worker-a",
    )

    assert result is provider_collision
    assert calls == ["leader"]
    assert follower["result"] == {
        "status": "RAIL_DEFERRED",
        "provider_called": False,
        "retry_after": 15,
        "operation": "publish-follower",
        "action": "update-pr-body",
        "repo": "owner/repo",
        "carrier": "owner/repo#123",
        "expected_head": HEAD,
    }
    assert availability_snapshot(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        now_epoch=1000.0,
    )["availability"] == "AVAILABLE"
