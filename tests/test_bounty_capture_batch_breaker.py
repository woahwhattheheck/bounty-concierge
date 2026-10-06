# SPDX-License-Identifier: MIT
"""Focused live-path checks for shared GitHub breaker admission."""

from time import time
from types import SimpleNamespace

import pytest
import requests

from concierge.bounty_capture_batch import _BatchSession
from concierge.github_breaker_ledger import GitHubBreakerLedger


def test_open_breaker_skips_provider_before_request(tmp_path):
    calls = []
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite",
        provider_route="token-primary",
        credential="credential",
    )
    ledger.record_receipt(
        "read-known-coordinate",
        "SECONDARY_RATE_LIMIT",
        observed_epoch=time(),
        retry_after_seconds=60,
    )
    session = SimpleNamespace(get=lambda url, **kwargs: calls.append(url))

    transport = _BatchSession(session, max_requests=5, breaker=ledger)
    with pytest.raises(requests.RequestException, match="breaker denied"):
        transport.get("https://api.github.com/repos/example/repo/issues/1")

    assert calls == []
    assert transport.request_count == 0
    assert transport.shared_breaker_deferred is True
    assert transport.failure == {"code": "RATE_LIMITED"}


def test_expired_breaker_probe_closes_after_healthy_response(tmp_path):
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite",
        provider_route="token-primary",
        credential="credential",
    )
    ledger.record_receipt(
        "read-known-coordinate",
        "SECONDARY_RATE_LIMIT",
        observed_epoch=time() - 10,
        retry_after_seconds=1,
    )
    response = SimpleNamespace(status_code=200, headers={})
    session = SimpleNamespace(get=lambda url, **kwargs: response)

    transport = _BatchSession(session, max_requests=5, breaker=ledger)
    assert transport.get("https://api.github.com/repos/example/repo/issues/1") is response
    assert transport.request_count == 1
    assert transport.breaker_probe_held is False
    assert ledger.admit(
        "read-known-coordinate", owner="next-worker"
    ).decision == "ALLOW"


def test_scope_denial_is_shared_without_a_second_provider_call(tmp_path):
    calls = []
    ledger = GitHubBreakerLedger(
        tmp_path / "breaker.sqlite",
        provider_route="github-app-installation-7",
        credential="credential",
    )

    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            status_code=403,
            headers={},
            json=lambda: {"message": "Resource not accessible by integration"},
        )

    first = _BatchSession(SimpleNamespace(get=get), max_requests=5, breaker=ledger)
    first.get("https://api.github.com/repos/example/repo/issues/1")
    assert first.failure == {"code": "HTTP_ERROR", "http_status": 403}

    second = _BatchSession(SimpleNamespace(get=get), max_requests=5, breaker=ledger)
    with pytest.raises(requests.RequestException, match="breaker denied"):
        second.get("https://api.github.com/repos/example/repo/issues/1")

    assert len(calls) == 1
    assert second.request_count == 0
    assert second.failure == {"code": "SCOPE_DENIED"}
