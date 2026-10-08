# SPDX-License-Identifier: MIT
"""Focused tests for GitHub publication admission before provider I/O."""

import pytest

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_publish_preflight import execute_publish_operation
from concierge.github_rail_availability import availability_snapshot


HEAD = "a" * 40


def _run(
    path,
    transport,
    *,
    operation,
    recovery_owner,
    action="update-pr-body",
    repo="owner/repo",
    scope_breaker_path=None,
):
    return execute_publish_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation=operation,
        action=action,
        repo=repo,
        carrier="owner/repo#123",
        expected_head=HEAD,
        transport=transport,
        recovery_owner=recovery_owner,
        scope_breaker_path=scope_breaker_path,
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


def test_integration_scope_denial_is_local_to_exact_repo_and_action(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    breaker_path = tmp_path / "breaker.sqlite"
    calls = []

    def denied_transport():
        calls.append("denied-provider")
        raise RuntimeError(
            "GitHub API error 403: Resource not accessible by integration"
        )

    with pytest.raises(RuntimeError, match="Resource not accessible by integration"):
        _run(
            path,
            denied_transport,
            operation="publish-first",
            recovery_owner="worker-a",
            scope_breaker_path=breaker_path,
        )

    repeated = _run(
        path,
        lambda: calls.append("repeated-provider"),
        operation="publish-repeat",
        recovery_owner="worker-b",
        scope_breaker_path=breaker_path,
    )
    assert calls == ["denied-provider"]
    assert repeated["status"] == "RAIL_DEFERRED"
    assert repeated["provider_called"] is False
    assert set(repeated) == {
        "status",
        "provider_called",
        "retry_after",
        "operation",
        "action",
        "repo",
        "carrier",
        "expected_head",
    }
    assert 1 <= repeated["retry_after"] <= 900

    assert _run(
        path,
        lambda: "other-repo-ok",
        operation="publish-other-repo",
        recovery_owner="worker-c",
        repo="owner/other",
        scope_breaker_path=breaker_path,
    ) == "other-repo-ok"

    assert _run(
        path,
        lambda: "other-action-ok",
        operation="publish-other-action",
        recovery_owner="worker-d",
        action="create-issue-comment",
        scope_breaker_path=breaker_path,
    ) == "other-action-ok"


def test_stale_handoff_reconciles_before_admission_or_publish(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    calls = []
    provider_match = {
        "pull_request": 52,
        "head_sha": HEAD,
        "head_branch": "sol56/backend34-husky-20261006",
        "issue_ref": "#34",
        "content_fingerprint": "sha256:exact-carrier",
    }

    result = execute_publish_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation="publish-stale-handoff",
        action="create-pull-request",
        repo="Owner/Repo",
        carrier="owner/repo#34",
        expected_head=HEAD.upper(),
        transport=lambda: calls.append("provider-write"),
        provider_reconcile=lambda: provider_match,
    )

    assert calls == []
    assert not path.exists()
    assert result == {
        "status": "PROVIDER_RECONCILED",
        "provider_called": False,
        "provider_reconcile_called": True,
        "provider_write_called": False,
        "operation": "publish-stale-handoff",
        "action": "create-pull-request",
        "repo": "Owner/Repo",
        "carrier": "owner/repo#34",
        "expected_head": HEAD,
        "provider_match": provider_match,
    }


def test_known_provider_capability_denial_reroutes_before_admission_or_publish(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    calls = []
    capability_evidence = {
        "actor": "actor-293",
        "target_repo": "Sponsor/Repo",
        "repository_access": False,
        "source": "installation-inventory",
    }

    def capability_denial():
        calls.append("capability-read")
        return capability_evidence

    result = execute_publish_operation(
        path,
        "credential",
        rail="github-app",
        actor="actor-293",
        operation="publish-capability-blocked",
        action="create-pull-request",
        repo="Sponsor/Repo",
        carrier="owner/repo#43",
        expected_head=HEAD,
        transport=lambda: calls.append("provider-write"),
        provider_capability_denial=capability_denial,
    )

    assert calls == ["capability-read"]
    assert not path.exists()
    assert result == {
        "status": "PROVIDER_REROUTE_REQUIRED",
        "provider_called": False,
        "provider_capability_called": True,
        "provider_write_called": False,
        "operation": "publish-capability-blocked",
        "action": "create-pull-request",
        "repo": "Sponsor/Repo",
        "carrier": "owner/repo#43",
        "expected_head": HEAD,
        "capability_evidence": capability_evidence,
    }


def test_pr_create_without_all_state_reconciliation_never_writes(tmp_path):
    """A stale open-PR scan is insufficient; missing fresh match check must stop."""
    path = tmp_path / "cooldown.sqlite"
    calls = []
    receipt = execute_publish_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation="same-head-second-publisher",
        action="create_pull_request",
        repo="Owner/Repo",
        carrier="Owner/Repo#32398",
        expected_head=HEAD,
        transport=lambda: calls.append("write"),
    )
    assert calls == []
    assert not path.exists()
    assert receipt["status"] == "PROVIDER_RECONCILIATION_REQUIRED"
    assert receipt["provider_write_called"] is False
    assert receipt["provider_reconcile_called"] is False
    assert receipt["expected_head"] == HEAD


def test_pr_create_with_current_provider_clear_check_can_write(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    calls = []
    result = execute_publish_operation(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        operation="verified-first-publisher",
        action="create-pull-request",
        repo="Owner/Repo",
        carrier="Owner/Repo#32398",
        expected_head=HEAD,
        provider_reconcile=lambda: (calls.append("all-states-reconcile") or None),
        transport=lambda: (calls.append("provider-write") or {"number": 32399}),
    )
    assert calls == ["all-states-reconcile", "provider-write"]
    assert result == {"number": 32399}
