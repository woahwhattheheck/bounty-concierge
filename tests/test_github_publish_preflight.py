# SPDX-License-Identifier: MIT
"""Focused tests for GitHub publication admission before provider I/O."""

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_publish_preflight import run_github_provider_operation


HEAD = "a" * 40


def _run(path, provider_call, *, operation_id, recovery_owner):
    return run_github_provider_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation_id=operation_id,
        carrier="owner/repo#123",
        expected_head=HEAD,
        provider_call=provider_call,
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
        operation_id="publish-123",
        recovery_owner="worker-a",
    )

    assert calls == []
    assert result == {
        "schema": "github-publish-preflight/v1",
        "decision": "RAIL_DEFERRED",
        "handoff_reason": "RAIL_DEFERRED",
        "rail": "private-token",
        "actor": "actor-293",
        "availability": "HOT",
        "retry_after_seconds": 200,
        "operation_id": "publish-123",
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

    def provider():
        calls.append("leader")
        follower["result"] = _run(
            path,
            lambda: calls.append("follower"),
            operation_id="publish-follower",
            recovery_owner="worker-b",
        )
        return {"provider": "ok"}

    result = _run(
        path,
        provider,
        operation_id="publish-leader",
        recovery_owner="worker-a",
    )

    assert result == {"provider": "ok"}
    assert calls == ["leader"]
    assert follower["result"]["decision"] == "RAIL_DEFERRED"
    assert follower["result"]["availability"] == "RECOVERY_PROBE_IN_FLIGHT"
    assert follower["result"]["operation_id"] == "publish-follower"
    assert GitHubCooldown(path, "credential").deadline() is None
