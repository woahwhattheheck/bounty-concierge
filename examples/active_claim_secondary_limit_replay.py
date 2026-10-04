#!/usr/bin/env python3
"""Offline prepared-request replay; no sockets, provider calls, or claims.

Run from the repository root with PYTHONPATH=. python examples/active_claim_secondary_limit_replay.py.
Use --observe to record counts on the parent source without repair assertions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from unittest.mock import patch

import requests
from concierge import active_claim_portfolio as portfolio

POLICY = {
    "version": "secondary-limit-replay-v1", "max_active_claims_total": 200,
    "max_active_claims_per_worker": 200, "max_active_claims_per_sponsor": 200,
    "sponsor_overrides": {}, "max_claim_age_seconds": 3600,
}
SECONDARY = b'{"message":"You have exceeded a secondary rate limit."}'
FORBIDDEN = b'{"message":"Resource not accessible by integration"}'
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)


def run_case(name, *, body=SECONDARY, headers=None, status=403, count=3,
             availability_first=False, connection_error=False):
    calls = []

    def send(_adapter, request, **_kwargs):
        calls.append(request.method)
        assert request.method == "GET"
        if connection_error:
            raise requests.ConnectionError("fixture transport error")
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.headers.update(headers or {})
        response._content = FORBIDDEN if availability_first and len(calls) == 1 else body
        response.encoding = "utf-8"
        return response

    candidates = [{"repo": "fixture/repo", "number": n + 1,
                   "sponsor_key": "fixture", "worker_id": "fixture-worker",
                   "reward_currency": "USD", "reward_minor": 10000}
                  for n in range(count)]
    # Exercise both real live readers and their real chained Requests errors.
    # Only the HTTP transport and the verifier clock are replaced.
    with patch.object(requests.adapters.HTTPAdapter, "send", send), \
         patch("socket.create_connection", side_effect=AssertionError("network forbidden")), \
         patch.object(portfolio, "_utc_now", return_value=NOW):
        receipt = portfolio.compile_live_active_claim_portfolio(candidates, [], POLICY)
    assert len(receipt["results"]) == count
    assert portfolio.verify_receipt_integrity(receipt)
    assert not any(row.get("dispatch") for row in receipt["results"])
    encoded = json.dumps(receipt)
    assert "Resource not accessible" not in encoded
    assert "You have exceeded" not in encoded
    return {"name": name, "prepared_requests": len(calls), "rows": count,
            "rate_limited_rows": sum("LIVE_PROVIDER_RATE_LIMITED" in row["reason_codes"]
                                     for row in receipt["results"]),
            "receipt_sha256": receipt["receipt_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe", action="store_true")
    args = parser.parse_args()
    scenarios = [
        ("secondary-no-headers", {}, 1),
        ("secondary-nonzero-remaining", {"headers": {"X-RateLimit-Remaining": "4999"}}, 1),
        ("secondary-from-availability", {"availability_first": True}, 2),
        ("secondary-100-candidates", {"count": 100}, 1),
        ("permission-403", {"body": FORBIDDEN}, 6),
        ("malformed-json", {"body": b"not-json"}, 6),
        ("nonobject-json", {"body": b'["secondary rate limit"]'}, 6),
        ("nonstring-message", {"body": b'{"message": ["rate limit"]}'}, 6),
        ("message-on-404", {"status": 404}, 6),
        ("transport-error", {"connection_error": True}, 6),
        ("header-403", {"body": FORBIDDEN, "headers": {"Retry-After": "60"}}, 1),
        ("http-429", {"status": 429, "body": b"not-json"}, 1),
    ]
    results = [run_case(name, **kwargs) for name, kwargs, _ in scenarios]
    # An exception's wording alone and a cyclic chain must not stop the batch.
    plain = RuntimeError("You have exceeded a secondary rate limit.")
    plain.__cause__ = plain
    assert portfolio._provider_rate_limited(plain) is False
    print(json.dumps({"network_calls": 0, "scenarios": results}, indent=2))
    if not args.observe:
        for result, (_, _, expected) in zip(results, scenarios):
            assert result["prepared_requests"] == expected, result
            assert bool(result["rate_limited_rows"]) == (expected < 6), result


if __name__ == "__main__":
    main()
