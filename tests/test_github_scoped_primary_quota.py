# SPDX-License-Identifier: MIT
"""Primary GitHub quota must stop all scoped readers for the same credential."""

from concierge import github_cooldown, github_rail_availability
from concierge.github_cooldown import GitHubCooldown
from concierge.github_read_preflight import GitHubReadFailure, execute_read_operation


def test_primary_throttle_is_credential_global_when_search_scoped(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(github_cooldown, "time", lambda: 1000.0)
    monkeypatch.setattr(github_rail_availability, "time", lambda: 1000.0)
    path = tmp_path / "limits.sqlite"

    def invoke(scope, transport):
        return execute_read_operation(
            path, "credential", rail="private-token", actor="actor-293",
            operation="read", operation_class="search",
            repo="owner/repo", target="issues", transport=transport,
            cooldown_scope=scope,
        )

    def limited():
        raise GitHubReadFailure("PRIMARY_RATE_LIMIT", reset_at=1300)

    first = invoke("search", limited)
    assert first["status"] == "READ_DEFERRED"
    assert first["retry_after"] == 300
    assert GitHubCooldown(path, "credential").quota_reserve_deadline() == 1300.0

    called = []
    second = invoke("issues", lambda: called.append(True))
    assert called == []
    assert second["status"] == "READ_DEFERRED"
    assert second["reason"] == "HOT"
    assert second["provider_called"] is False
    assert second["retry_after"] == 300
