from __future__ import annotations

import hashlib
import unittest

import requests

from concierge.revenue_closeout import (
    RevenueCloseoutInputError,
    build_closeout_queue,
    scan_paid_pr,
)


SHA = "a" * 40
ROUTE = "https://example.test/settlement/acme-widgets-17"
PR_URL = "https://github.com/acme/widgets/pull/17"
MERGED_AT = "2026-09-12T03:00:00Z"
SENT_AT = "2026-09-12T04:00:00Z"


class FakeResponse:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            response = requests.Response()
            response.status_code = self.status
            raise requests.HTTPError(f"status {self.status}", response=response)

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
            value = self.routes[key]
        elif url in self.routes:
            value = self.routes[url]
        else:
            raise AssertionError(f"unexpected GET {url} params={params}")
        return value if isinstance(value, FakeResponse) else FakeResponse(value)


def item(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 17,
        "operator_login": "builder",
        "advertised_amount": "250.00",
        "currency": "RTC",
        "last_seen_at": "2026-09-12T00:00:00Z",
    }
    value.update(overrides)
    return value


def pr_payload(**overrides):
    value = {
        "html_url": PR_URL,
        "state": "closed",
        "merged_at": MERGED_AT,
        "user": {"login": "builder"},
        "head": {"sha": SHA},
    }
    value.update(overrides)
    return value


def routes(pr=None, *, reviews=None, comments=None):
    base = "https://api.github.com/repos/acme/widgets"
    return {
        f"{base}/pulls/17": pr or pr_payload(),
        (
            f"{base}/pulls/17/reviews",
            (("page", 1), ("per_page", 100)),
        ): reviews or [],
        (
            f"{base}/issues/17/comments",
            (("page", 1), ("per_page", 100)),
        ): comments or [],
    }


def evidence(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 17,
        "provider": "gmail",
        "receipt_ref": "gmail-message-abc123",
        "receipt_sha256": "b" * 64,
        "sent_at": SENT_AT,
        "settlement_route_sha256": hashlib.sha256(ROUTE.encode("utf-8")).hexdigest(),
    }
    value.update(overrides)
    return value


class RevenueCloseoutContactEvidenceTests(unittest.TestCase):
    def test_route_metadata_alone_does_not_suppress_collection_action(self):
        result = scan_paid_pr(
            item(settlement_followup_url=ROUTE),
            session=FakeSession(routes()),
        )
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["reason"], "merged_route_metadata_without_send_evidence")
        self.assertFalse(result["settlement_followup_send_evidenced"])
        self.assertFalse(result["settlement_route_proves_prior_contact"])

    def test_missing_route_still_requires_followup_routing(self):
        result = scan_paid_pr(item(), session=FakeSession(routes()))
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["reason"], "merged_without_settlement_route")
        self.assertFalse(result["settlement_followup_send_evidenced"])

    def test_bound_post_merge_send_receipt_allows_monitoring(self):
        receipt = evidence()
        result = scan_paid_pr(
            item(
                settlement_followup_url=ROUTE,
                settlement_followup_evidence=receipt,
            ),
            session=FakeSession(routes()),
        )
        self.assertEqual(result["next_action"], "monitor_settlement")
        self.assertEqual(result["reason"], "merged_followup_send_evidenced")
        self.assertTrue(result["settlement_followup_send_evidenced"])
        self.assertEqual(result["settlement_followup_evidence"], receipt)
        self.assertFalse(result["settlement_route_proves_prior_contact"])

    def test_route_hash_mismatch_rejects_before_network(self):
        session = FakeSession({})
        with self.assertRaisesRegex(RevenueCloseoutInputError, "not bound to the configured settlement route"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(
                        settlement_route_sha256="c" * 64,
                    ),
                ),
                session=session,
            )
        self.assertEqual(session.calls, [])

    def test_cross_pr_receipt_transplant_rejects_before_network(self):
        session = FakeSession({})
        with self.assertRaisesRegex(RevenueCloseoutInputError, "cannot be transplanted"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(pr=18),
                ),
                session=session,
            )
        self.assertEqual(session.calls, [])

    def test_receipt_requires_route_metadata(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "requires settlement_followup_url"):
            scan_paid_pr(
                item(settlement_followup_evidence=evidence()),
                session=FakeSession({}),
            )

    def test_pre_merge_send_evidence_rejects(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "cannot predate the merge"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(sent_at="2026-09-12T02:59:59Z"),
                ),
                session=FakeSession(routes()),
            )

    def test_future_send_evidence_rejects(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "cannot be in the future"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(sent_at="2099-01-01T00:00:00Z"),
                ),
                session=FakeSession(routes()),
            )

    def test_unmerged_work_cannot_carry_settlement_send_evidence(self):
        open_pr = pr_payload(state="open", merged_at=None)
        with self.assertRaisesRegex(RevenueCloseoutInputError, "only valid for currently merged work"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(),
                ),
                session=FakeSession(routes(pr=open_pr)),
            )

    def test_maintainer_change_request_still_outranks_evidenced_collection(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-12T05:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
                "html_url": PR_URL + "#pullrequestreview-1",
            }
        ]
        result = scan_paid_pr(
            item(
                settlement_followup_url=ROUTE,
                settlement_followup_evidence=evidence(),
            ),
            session=FakeSession(routes(reviews=reviews)),
        )
        self.assertEqual(result["next_action"], "repair_requested")
        self.assertEqual(result["reason"], "current_maintainer_changes_requested")
        self.assertTrue(result["settlement_followup_send_evidenced"])

    def test_maintainer_comment_still_outranks_missing_collection_receipt(self):
        comments = [
            {
                "updated_at": "2026-09-12T05:00:00Z",
                "author_association": "OWNER",
                "user": {"login": "maintainer"},
                "html_url": PR_URL + "#issuecomment-1",
            }
        ]
        result = scan_paid_pr(
            item(settlement_followup_url=ROUTE),
            session=FakeSession(routes(comments=comments)),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(result["reason"], "new_maintainer_feedback")

    def test_receipt_shape_is_exact_and_hash_is_lowercase(self):
        bad_extra = evidence()
        bad_extra["freeform"] = "not allowed"
        with self.assertRaisesRegex(RevenueCloseoutInputError, "exact receipt-binding keys"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=bad_extra,
                ),
                session=FakeSession({}),
            )
        with self.assertRaisesRegex(RevenueCloseoutInputError, "lowercase SHA-256"):
            scan_paid_pr(
                item(
                    settlement_followup_url=ROUTE,
                    settlement_followup_evidence=evidence(receipt_sha256="B" * 64),
                ),
                session=FakeSession({}),
            )

    def test_queue_uses_public_evidence_bound_scanner(self):
        result = build_closeout_queue(
            [item(settlement_followup_url=ROUTE)],
            session=FakeSession(routes()),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["next_action"], "route_settlement_followup")
        self.assertEqual(result[0]["reason"], "merged_route_metadata_without_send_evidence")


if __name__ == "__main__":
    unittest.main()
