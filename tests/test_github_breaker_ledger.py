# SPDX-License-Identifier: MIT
"""Focused concurrency and isolation checks for the shared GitHub breaker ledger."""

from concierge.github_breaker_ledger import (
    AUTH_FAILED,
    OPEN_UNTIL,
    SCOPE_DENIED,
    GitHubBreakerLedger,
)


def test_hundred_workers_share_one_recovery_probe(tmp_path):
    path = tmp_path / "breaker.sqlite"
    ledger = GitHubBreakerLedger(path, provider_route="token-primary", credential="secret")
    ledger.record_receipt(
        "search", "SECONDARY_RATE_LIMIT", observed_epoch=1000.0, retry_after_seconds=10
    )

    decisions = [
        ledger.admit("search", owner=f"worker-{index}", now_epoch=1011.0).decision
        for index in range(100)
    ]
    assert decisions.count("PROBE") == 1
    assert decisions.count("SKIP") == 99


def test_primary_exhaustion_does_not_freeze_healthy_app_route(tmp_path):
    path = tmp_path / "breaker.sqlite"
    token = GitHubBreakerLedger(path, provider_route="token-primary", credential="token")
    app = GitHubBreakerLedger(path, provider_route="github-app-installation-7", credential="app")

    token.record_receipt(
        "search", "PRIMARY_RATE_LIMIT", observed_epoch=1000.0, reset_epoch=1400.0
    )

    assert token.admit("search", owner="token-worker", now_epoch=1100.0).decision == "SKIP"
    assert app.admit(
        "read-known-coordinate", owner="app-worker", now_epoch=1100.0
    ).decision == "ALLOW"


def test_scope_denied_is_not_a_quota_breaker(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite", provider_route="github-app", credential="app"
    )
    ledger.record_receipt("write", "SCOPE_DENIED", observed_epoch=1000.0)
    admission = ledger.admit("write", owner="worker", now_epoch=5000.0)

    assert admission.state == SCOPE_DENIED
    assert admission.reason == "SCOPE_DENIED"
    assert admission.decision == "SKIP"
    assert admission.until_epoch is None


def test_later_longer_reset_extends_deadline_monotonically(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite", provider_route="token-primary", credential="token"
    )
    ledger.record_receipt(
        "fork", "SECONDARY_RATE_LIMIT", observed_epoch=1000.0, retry_after_seconds=30
    )
    ledger.record_receipt(
        "fork", "SECONDARY_RATE_LIMIT", observed_epoch=1005.0, reset_epoch=1200.0
    )
    ledger.record_receipt(
        "fork", "SECONDARY_RATE_LIMIT", observed_epoch=1010.0, retry_after_seconds=20
    )

    admission = ledger.admit("fork", owner="worker", now_epoch=1100.0)
    assert admission.state == OPEN_UNTIL
    assert admission.until_epoch == 1200.0
    assert admission.retry_after_seconds == 100


def test_successful_probe_closes_only_its_generation(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite", provider_route="token-primary", credential="token"
    )
    ledger.record_receipt(
        "search", "SECONDARY_RATE_LIMIT", observed_epoch=1000.0, retry_after_seconds=1
    )
    assert ledger.admit("search", owner="probe", now_epoch=1002.0).decision == "PROBE"

    ledger.record_receipt(
        "search", "SECONDARY_RATE_LIMIT", observed_epoch=1003.0, reset_epoch=1200.0
    )
    assert (
        ledger.complete_probe("search", owner="probe", success=True, now_epoch=1004.0)
        is False
    )
    assert ledger.admit("search", owner="next", now_epoch=1100.0).decision == "SKIP"


def test_stale_terminal_state_is_cleaned_up(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite",
        provider_route="github-app",
        credential="app",
        stale_ttl_seconds=100,
    )
    ledger.record_receipt("write", "AUTH_FAILED", observed_epoch=1000.0)
    assert ledger.admit("write", owner="worker", now_epoch=1050.0).state == AUTH_FAILED
    assert ledger.cleanup_stale(now_epoch=1200.0) == 1
    assert ledger.admit("write", owner="worker", now_epoch=1200.0).decision == "ALLOW"

def test_failed_probe_retains_cooldown_until_lease_expires(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite", provider_route="github-app", credential="app"
    )
    ledger.record_receipt(
        "search", "SECONDARY_RATE_LIMIT", observed_epoch=1000.0, retry_after_seconds=1
    )
    first = ledger.admit(
        "search", owner="worker-a", now_epoch=1002.0, lease_seconds=5.0
    )
    assert first.decision == "PROBE"
    assert first.until_epoch == 1007.0

    assert ledger.complete_probe(
        "search", owner="worker-a", success=False, now_epoch=1003.0
    )

    during_cooldown = [
        ledger.admit("search", owner=owner, now_epoch=1004.0)
        for owner in ("worker-a", "worker-b")
    ]
    assert [item.decision for item in during_cooldown] == ["SKIP", "SKIP"]
    assert {item.until_epoch for item in during_cooldown} == {1007.0}

    after_cooldown = [
        ledger.admit("search", owner=owner, now_epoch=1008.0)
        for owner in ("worker-a", "worker-b")
    ]
    assert [item.decision for item in after_cooldown].count("PROBE") == 1
    assert [item.decision for item in after_cooldown].count("SKIP") == 1

