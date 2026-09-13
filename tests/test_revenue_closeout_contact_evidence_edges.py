from __future__ import annotations

import hashlib
import unittest

import requests

from concierge.revenue_closeout import RevenueCloseoutInputError, scan_paid_pr


ROUTE = "https://example.test/settlement/acme-widgets-1"
SHA = "a" * 40


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, *, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        key = (url, None if params is None else tuple(sorted(params.items())))
        if key in self.routes:
            return FakeResponse(self.routes[key])
        if url in self.routes:
            return FakeResponse(self.routes[url])
        raise AssertionError(f"unexpected GET {url} params={params}")


def item(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 1,
        "operator_login": "builder",
        "advertised_amount": "250",
        "currency": "RTC",
        "last_seen_at": "2026-09-12T00:00:00Z",
        "settlement_followup_url": ROUTE,
    }
    value.update(overrides)
    return value


def receipt(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 1,
        "provider": "gmail",
        "receipt_ref": "receipt-1",
        "receipt_sha256": "b" * 64,
        "sent_at": "2026-09-12T04:00:00Z",
        "settlement_route_sha256": hashlib.sha256(ROUTE.encode()).hexdigest(),
    }
    value.update(overrides)
    return value


def merged_routes(*, head=SHA):
    base = "https://api.github.com/repos/acme/widgets"
    return {
        f"{base}/pulls/1": {
            "html_url": "https://github.com/acme/widgets/pull/1",
            "state": "closed",
            "merged_at": "2026-09-12T03:00:00Z",
            "user": {"login": "builder"},
            "head": {"sha": head},
        },
        (
            f"{base}/pulls/1/reviews",
            (("page", 1), ("per_page", 100)),
        ): [],
        (
            f"{base}/issues/1/comments",
            (("page", 1), ("per_page", 100)),
        ): [],
    }


class ContactEvidenceEdgeTests(unittest.TestCase):
    def test_bool_pr_identity_cannot_alias_integer_pr_one(self):
        session = FakeSession({})
        with self.assertRaisesRegex(RevenueCloseoutInputError, "cannot be transplanted"):
            scan_paid_pr(
                item(settlement_followup_evidence=receipt(pr=True)),
                session=session,
            )
        self.assertEqual(session.calls, [])

    def test_head_moved_never_promotes_unchecked_receipt_to_current_evidence(self):
        session = FakeSession(
            {
                "https://api.github.com/repos/acme/widgets/pulls/1": {
                    "html_url": "https://github.com/acme/widgets/pull/1",
                    "state": "closed",
                    "merged_at": "2026-09-12T03:00:00Z",
                    "user": {"login": "builder"},
                    "head": {"sha": "c" * 40},
                }
            }
        )
        result = scan_paid_pr(
            item(
                expected_head_sha=SHA,
                settlement_followup_evidence=receipt(),
            ),
            session=session,
        )
        self.assertEqual(result["state"], "HEAD_MOVED")
        self.assertFalse(result["settlement_followup_send_evidenced"])
        self.assertIsNotNone(result["settlement_followup_evidence"])
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
