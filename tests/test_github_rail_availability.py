# SPDX-License-Identifier: MIT
"""Focused tests for read-only rail/account GitHub availability snapshots."""

from concierge import github_cooldown
from concierge.github_cooldown import GitHubCooldown
from concierge.github_rail_availability import availability_snapshot


def test_snapshot_combines_route_cooldown_and_global_quota(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    token = "test-credential"
    search = GitHubCooldown(path, token, cooldown_scope="search")
    search.extend(1200.0)
    search.reserve_quota_until(1500.0)

    snapshot = availability_snapshot(
        path,
        token,
        rail="managed-app",
        actor="actor-293",
        cooldown_scope="search",
        now_epoch=1000.0,
    )

    assert snapshot["availability"] == "HOT"
    assert snapshot["blocked"] is True
    assert snapshot["retry_after_seconds"] == 500
    assert snapshot["provider_cooldown"]["retry_after_seconds"] == 200
    assert snapshot["quota_reservation"]["retry_after_seconds"] == 500
    assert snapshot["recovery_lease"]["active"] is False


def test_expired_global_cooldown_is_recovery_ready_without_claiming(tmp_path, monkeypatch):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    cooldown.extend(900.0)

    snapshot = availability_snapshot(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        now_epoch=1000.0,
    )

    assert snapshot["availability"] == "RECOVERY_READY"
    assert snapshot["blocked"] is False
    assert cooldown.claim_recovery_probe("worker-a") == (True, True, 1015.0)


def test_live_recovery_probe_is_visible_to_followers(tmp_path, monkeypatch):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    cooldown.extend(900.0)
    assert cooldown.claim_recovery_probe("worker-a") == (True, True, 1015.0)

    snapshot = availability_snapshot(
        path,
        "credential",
        rail="private-token",
        actor="actor-293",
        now_epoch=1001.0,
    )

    assert snapshot["availability"] == "RECOVERY_PROBE_IN_FLIGHT"
    assert snapshot["blocked"] is True
    assert snapshot["retry_after_seconds"] == 14
    assert snapshot["recovery_lease"] == {
        "active": True,
        "until_epoch": 1015.0,
        "retry_after_seconds": 14,
    }


def test_expired_route_scoped_secondary_is_immediately_available(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential", cooldown_scope="search")
    cooldown.extend(900.0)

    snapshot = availability_snapshot(
        path,
        "credential",
        rail="managed-app",
        actor="actor-293",
        cooldown_scope="search",
        now_epoch=1000.0,
    )

    assert snapshot["availability"] == "AVAILABLE"
    assert snapshot["blocked"] is False
    assert snapshot["recovery_lease"]["active"] is False


def test_absent_store_is_available_and_never_exposes_credential(tmp_path):
    snapshot = availability_snapshot(
        tmp_path / "missing.sqlite",
        "credential-material",
        rail="managed-app",
        actor="actor-293",
        cooldown_scope="search",
        now_epoch=1000.0,
    )

    assert snapshot["state"] == "ABSENT"
    assert snapshot["availability"] == "AVAILABLE"
    assert "credential-material" not in repr(snapshot)
