# SPDX-License-Identifier: MIT
"""Focused concurrency checks for serialized GitHub cooldown recovery."""

from types import SimpleNamespace

import pytest
import requests

from concierge import bounty_capture_batch, github_cooldown
from concierge.bounty_capture_batch import _BatchSession
from concierge.github_cooldown import GitHubCooldown


def test_recovery_lease_serializes_takeover_and_preserves_newer_deadline(
    tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    path = tmp_path / "cooldown.sqlite"
    first = GitHubCooldown(path, "credential")
    second = GitHubCooldown(path, "credential")

    first.extend(900.0)
    assert first.claim_recovery_probe("worker-a") == (True, True, 1015.0)
    assert second.claim_recovery_probe("worker-b") == (True, False, 1015.0)

    clock[0] = 1016.0
    required, acquired, lease_until = second.claim_recovery_probe("worker-b")
    assert required and acquired and lease_until == 1031.0
    assert first.complete_recovery_probe("worker-a") is False

    first.extend(1100.0)
    assert second.complete_recovery_probe("worker-b") is False
    assert second.deadline() == 1100.0


def test_route_scoped_secondary_cooldown_does_not_take_account_recovery_lease(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    cooldown = GitHubCooldown(
        tmp_path / "cooldown.sqlite", "credential", cooldown_scope="search",
    )
    cooldown.extend(900.0)

    assert cooldown.claim_recovery_probe("worker") == (False, False, None)
    assert cooldown.deadline() == 900.0


def test_batch_defers_follower_then_clears_stale_cooldown_on_healthy_probe(
    tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    monkeypatch.setattr(bounty_capture_batch, "time", lambda: clock[0])
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    cooldown.extend(900.0)
    assert cooldown.claim_recovery_probe("other-worker") == (True, True, 1015.0)

    calls = []
    healthy = SimpleNamespace(status_code=200, headers={})
    session = SimpleNamespace(get=lambda url, **kwargs: calls.append(url) or healthy)
    follower = _BatchSession(session, max_requests=4, cooldown=cooldown)

    with pytest.raises(requests.RequestException, match="recovery probe already in flight"):
        follower.get("https://api.github.com/repos/example/one/issues/1")
    assert calls == []
    assert follower.failure == {"code": "RECOVERY_DEFERRED"}
    assert follower.shared_recovery_deferred is True
    assert follower.retry_after_seconds == 15

    clock[0] = 1016.0
    probe = _BatchSession(session, max_requests=4, cooldown=cooldown)
    assert probe.get("https://api.github.com/repos/example/one/issues/1") is healthy
    assert calls == ["https://api.github.com/repos/example/one/issues/1"]
    assert cooldown.deadline() is None


def test_failed_recovery_probe_extends_shared_cooldown_before_release(
    tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    monkeypatch.setattr(bounty_capture_batch, "time", lambda: clock[0])
    cooldown = GitHubCooldown(tmp_path / "cooldown.sqlite", "credential")
    cooldown.extend(900.0)

    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    session = SimpleNamespace(get=fail)
    probe = _BatchSession(session, max_requests=4, cooldown=cooldown)
    with pytest.raises(requests.ConnectionError):
        probe.get("https://api.github.com/repos/example/one/issues/1")

    assert cooldown.deadline() == 1060.0
    assert probe.recovery_probe_held is False

def test_batch_honors_cooldown_refreshed_during_recovery_claim(
    tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    monkeypatch.setattr(bounty_capture_batch, "time", lambda: clock[0])
    path = tmp_path / "cooldown.sqlite"
    cooldown = GitHubCooldown(path, "credential")
    peer = GitHubCooldown(path, "credential")
    cooldown.extend(900.0)

    original_claim = cooldown.claim_recovery_probe

    def claim_after_refresh(owner):
        peer.extend(1030.1)
        return original_claim(owner)

    monkeypatch.setattr(cooldown, "claim_recovery_probe", claim_after_refresh)

    calls = []
    healthy = SimpleNamespace(status_code=200, headers={})
    session = SimpleNamespace(get=lambda url, **kwargs: calls.append(url) or healthy)
    batch = _BatchSession(session, max_requests=4, cooldown=cooldown)

    with pytest.raises(requests.RequestException, match="shared provider cooldown active"):
        batch.get("https://api.github.com/repos/example/one/issues/1")

    assert calls == []
    assert batch.rate_limited is True
    assert batch.shared_cooldown_deferred is True
    assert batch.failure == {"code": "RATE_LIMITED"}
    assert batch.retry_after_seconds == 31
    assert cooldown.deadline() == 1030.1

