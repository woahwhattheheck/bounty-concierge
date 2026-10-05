# SPDX-License-Identifier: MIT
"""Focused checks for the zero-provider-call cooldown status surface."""
import json

from concierge.github_cooldown import GitHubCooldown
from concierge import github_cooldown_status as status


def test_status_snapshot_reports_effective_local_deadline(tmp_path):
    path = tmp_path / "github.sqlite"
    store = GitHubCooldown(path, "shared-token")
    store.extend(1120.2)
    store.reserve_quota_until(1060.0)

    result = status.status_snapshot(path, "shared-token", now_epoch=1000.0)

    assert result["state"] == "READY"
    assert result["blocked"] is True
    assert result["retry_after_seconds"] == 121
    assert result["effective_until_epoch"] == 1120.2
    assert result["provider_cooldown"] == {
        "active": True,
        "until_epoch": 1120.2,
        "retry_after_seconds": 121,
    }
    assert result["quota_reservation"] == {
        "active": True,
        "until_epoch": 1060.0,
        "retry_after_seconds": 60,
    }
    assert "shared-token" not in json.dumps(result)
    assert str(path) not in json.dumps(result)


def test_missing_store_is_observed_without_creation(tmp_path):
    path = tmp_path / "missing.sqlite"

    result = status.status_snapshot(path, "shared-token", now_epoch=1000.0)

    assert result["state"] == "ABSENT"
    assert result["blocked"] is False
    assert result["retry_after_seconds"] == 0
    assert not path.exists()


def test_expired_deadlines_are_not_blocking(tmp_path):
    path = tmp_path / "github.sqlite"
    store = GitHubCooldown(path, None)
    store.extend(900.0)
    store.reserve_quota_until(950.0)

    result = status.status_snapshot(path, None, now_epoch=1000.0)

    assert result["state"] == "READY"
    assert result["blocked"] is False
    assert result["effective_until_epoch"] is None
    assert result["provider_cooldown"]["retry_after_seconds"] == 0
    assert result["quota_reservation"]["retry_after_seconds"] == 0
