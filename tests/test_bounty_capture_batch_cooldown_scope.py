# SPDX-License-Identifier: MIT
"""Focused composition coverage for route-scoped capture cooldowns."""

from types import SimpleNamespace

import pytest
import requests

from concierge import bounty_capture_batch, github_cooldown


def test_collect_batch_routes_scoped_cooldown_and_reports_it(tmp_path, monkeypatch):
    calls = []

    class FakeCooldown:
        def __init__(self, path, token, *, cooldown_scope=None):
            calls.append((path, token, cooldown_scope))

    monkeypatch.setattr(bounty_capture_batch, "GitHubCooldown", FakeCooldown)

    cooldown_file = tmp_path / "cooldown.sqlite"
    summary = bounty_capture_batch.collect_batch(
        [],
        tmp_path / "run",
        token="test-token",
        cooldown_file=cooldown_file,
        cooldown_scope="search",
    )

    assert calls == [(cooldown_file, "test-token", "search")]
    assert summary["shared_cooldown"]["enabled"] is True
    assert summary["shared_cooldown"]["scoped"] is True

    with pytest.raises(ValueError, match="cooldown_scope requires cooldown_file"):
        bounty_capture_batch.collect_batch(
            [],
            tmp_path / "invalid-run",
            cooldown_scope="search",
        )

def test_expired_global_cooldown_serializes_and_backs_off_recovery_probe(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr(bounty_capture_batch, "time", lambda: clock[0])
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])

    path = tmp_path / "cooldown.sqlite"
    leader_store = github_cooldown.GitHubCooldown(path, "test-token")
    leader_store.extend(900.0)
    assert leader_store.claim_recovery_probe("leader") == (True, True, 1015.0)

    sent: list[str] = []
    follower = bounty_capture_batch._BatchSession(
        SimpleNamespace(),
        max_requests=10,
        cooldown=github_cooldown.GitHubCooldown(path, "test-token"),
    )
    with pytest.raises(requests.RequestException, match="recovery probe already leased"):
        follower._request(
            lambda target, **kwargs: sent.append(target),
            "https://api.github.com/repos/example/one/issues/1",
        )
    assert sent == []
    assert follower.shared_recovery_deferred is True
    assert follower.retry_after_seconds == 15

    clock[0] = 1016.0
    failed_probe = bounty_capture_batch._BatchSession(
        SimpleNamespace(),
        max_requests=10,
        cooldown=github_cooldown.GitHubCooldown(path, "test-token"),
    )

    def fail_transport(target, **kwargs):
        raise requests.ConnectionError("offline replay")

    with pytest.raises(requests.ConnectionError):
        failed_probe._request(
            fail_transport,
            "https://api.github.com/repos/example/one/issues/1",
        )
    assert failed_probe.shared_recovery_probe is True
    assert github_cooldown.GitHubCooldown(path, "test-token").deadline() == 1076.0

    clock[0] = 1076.0
    recovered = bounty_capture_batch._BatchSession(
        SimpleNamespace(),
        max_requests=10,
        cooldown=github_cooldown.GitHubCooldown(path, "test-token"),
    )
    response = SimpleNamespace(status_code=200, headers={})
    assert (
        recovered._request(
            lambda target, **kwargs: response,
            "https://api.github.com/repos/example/one/issues/1",
        )
        is response
    )
    assert recovered.shared_recovery_probe is True
    assert github_cooldown.GitHubCooldown(path, "test-token").deadline() is None

