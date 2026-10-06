# SPDX-License-Identifier: MIT
"""Focused composition coverage for route-scoped capture cooldowns."""

import pytest

from concierge import bounty_capture_batch


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
