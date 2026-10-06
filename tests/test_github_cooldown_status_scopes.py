# SPDX-License-Identifier: MIT
"""Focused regression for route-scoped cooldown status reads."""

from concierge.github_cooldown import GitHubCooldown
from concierge.github_cooldown_status import status_snapshot


def test_status_snapshot_reads_selected_route_scope_and_global_quota(tmp_path):
    path = tmp_path / "cooldown.sqlite"
    token = "test-credential"

    search = GitHubCooldown(path, token, cooldown_scope="search")
    core = GitHubCooldown(path, token, cooldown_scope="core")

    search.extend(1200.0)
    search.reserve_quota_until(1500.0)

    search_status = status_snapshot(
        path, token, cooldown_scope="search", now_epoch=1000.0
    )
    core_status = status_snapshot(
        path, token, cooldown_scope="core", now_epoch=1000.0
    )

    assert search_status["provider_cooldown"] == {
        "active": True,
        "until_epoch": 1200.0,
        "retry_after_seconds": 200,
    }
    assert core_status["provider_cooldown"] == {
        "active": False,
        "until_epoch": None,
        "retry_after_seconds": 0,
    }
    assert search_status["quota_reservation"] == core_status["quota_reservation"] == {
        "active": True,
        "until_epoch": 1500.0,
        "retry_after_seconds": 500,
    }
    assert search_status["effective_until_epoch"] == 1500.0
    assert core_status["effective_until_epoch"] == 1500.0
