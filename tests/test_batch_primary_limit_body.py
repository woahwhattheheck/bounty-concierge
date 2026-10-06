# SPDX-License-Identifier: MIT
"""Focused classification check for body-confirmed primary GitHub limits."""

from types import SimpleNamespace

from concierge import bounty_capture_batch, github_cooldown
from concierge.bounty_capture_batch import _BatchSession
from concierge.github_cooldown import GitHubCooldown


def test_primary_limit_body_preserves_reset_without_remaining_header(
    tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(github_cooldown, "time", lambda: clock[0])
    monkeypatch.setattr(bounty_capture_batch, "time", lambda: clock[0])

    response = SimpleNamespace(
        status_code=403,
        headers={"Retry-After": "5", "X-RateLimit-Reset": "1120"},
        json=lambda: {"message": "API rate limit exceeded for user ID 123."},
    )
    session = SimpleNamespace(get=lambda url, **kwargs: response)
    cooldown = GitHubCooldown(tmp_path / "cooldown.sqlite", "credential")
    transport = _BatchSession(session, max_requests=1, cooldown=cooldown)

    assert transport.get("https://api.github.com/repos/example/repo/issues/1") is response
    assert transport.rate_limited is True
    assert transport.retry_after_seconds == 5
    assert transport.rate_limit_reset_at == 1120
    assert transport.shared_unknown_backoff_seconds is None
    assert cooldown.deadline() == 1120.0
