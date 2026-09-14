from __future__ import annotations

import hashlib
import unittest

import requests

from concierge.revenue_closeout import (
    RevenueCloseoutError,
    RevenueCloseoutInputError,
    scan_paid_pr,
)


SHA = "a" * 40
PR_URL = "https://github.com/acme/widgets/pull/17"
BASE = "https://api.github.com/repos/acme/widgets"


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
        "pr": 17,
        "operator_login": "builder",
        "advertised_amount": "250.00",
        "currency": "USD",
        "last_seen_at": "2026-09-13T01:30:00Z",
    }
    value.update(overrides)
    return value


def review(body: str, *, review_id: int = 501, submitted_at: str = "2026-09-13T01:00:00Z"):
    return {
        "id": review_id,
        "state": "COMMENTED",
        "body": body,
        "submitted_at": submitted_at,
        "author_association": "MEMBER",
        "user": {"login": "maintainer", "type": "User"},
        "html_url": PR_URL + f"#pullrequestreview-{review_id}",
    }


def routes(*, reviews=None, issue_comments=None, inline_comments=None):
    page = (("page", 1), ("per_page", 100))
    return {
        f"{BASE}/pulls/17": {
            "html_url": PR_URL,
            "state": "closed",
            "merged_at": "2026-09-13T03:00:00Z",
            "updated_at": "2026-09-13T03:00:00Z",
            "user": {"login": "builder"},
            "head": {"sha": SHA},
        },
        (f"{BASE}/pulls/17/reviews", page): reviews or [],
        (f"{BASE}/pulls/17/comments", page): inline_comments or [],
        (f"{BASE}/issues/17/comments", page): issue_comments or [],
    }


def ack(review_id: int, body: str):
    return {
        "review_id": review_id,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


class RevenueCloseoutReviewBodyGenerationTests(unittest.TestCase):
    def test_old_nonempty_review_body_without_ack_blocks_settlement(self):
        body = "Please add a regression test."
        result = scan_paid_pr(
            item(),
            session=FakeSession(routes(reviews=[review(body)])),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(
            result["reason"],
            "unacknowledged_maintainer_review_body_generation",
        )
        self.assertEqual(result["new_feedback_count"], 0)
        self.assertEqual(result["unacknowledged_review_body_count"], 1)
        receipt = result["unacknowledged_review_body_generations"][0]
        self.assertEqual(receipt["review_id"], 501)
        self.assertEqual(receipt["body_sha256"], ack(501, body)["body_sha256"])
        self.assertEqual(receipt["submitted_at"], "2026-09-13T01:00:00Z")

    def test_exact_acknowledged_generation_may_route_settlement(self):
        body = "Please add a regression test."
        result = scan_paid_pr(
            item(acknowledged_review_bodies=[ack(501, body)]),
            session=FakeSession(routes(reviews=[review(body)])),
        )
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["reason"], "merged_without_settlement_route")
        self.assertEqual(result["acknowledged_review_body_count"], 1)
        self.assertEqual(result["unacknowledged_review_body_count"], 0)

    def test_post_cursor_body_edit_reopens_same_review_id(self):
        handled_body = "Looks okay."
        edited_body = "Looks okay. Please add the missing rollback test."
        result = scan_paid_pr(
            item(acknowledged_review_bodies=[ack(501, handled_body)]),
            session=FakeSession(routes(reviews=[review(edited_body)])),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(
            result["reason"],
            "unacknowledged_maintainer_review_body_generation",
        )
        self.assertEqual(result["new_feedback_count"], 0)
        receipt = result["unacknowledged_review_body_generations"][0]
        self.assertEqual(receipt["review_id"], 501)
        self.assertEqual(receipt["body_sha256"], ack(501, edited_body)["body_sha256"])
        self.assertNotEqual(receipt["body_sha256"], ack(501, handled_body)["body_sha256"])

    def test_acknowledged_parent_does_not_hide_fresh_inline_obligation(self):
        body = "Summary already handled."
        inline = {
            "id": 700,
            "pull_request_review_id": 501,
            "body": "Please change this line.",
            "updated_at": "2026-09-13T02:00:00Z",
            "author_association": "MEMBER",
            "user": {"login": "maintainer", "type": "User"},
            "html_url": PR_URL + "#discussion_r700",
        }
        result = scan_paid_pr(
            item(acknowledged_review_bodies=[ack(501, body)]),
            session=FakeSession(
                routes(reviews=[review(body)], inline_comments=[inline])
            ),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(result["reason"], "new_maintainer_feedback")
        self.assertEqual(result["acknowledged_review_body_count"], 1)

    def test_malformed_acknowledgement_rejects_before_network(self):
        bad_values = [
            "not-a-list",
            [{"review_id": 501}],
            [{"review_id": True, "body_sha256": "b" * 64}],
            [{"review_id": 501, "body_sha256": "B" * 64}],
            [ack(501, "a"), ack(501, "b")],
        ]
        for bad in bad_values:
            with self.subTest(bad=bad):
                session = FakeSession({})
                with self.assertRaises(RevenueCloseoutInputError):
                    scan_paid_pr(
                        item(acknowledged_review_bodies=bad),
                        session=session,
                    )
                self.assertEqual(session.calls, [])

    def test_nonempty_review_without_stable_id_fails_closed(self):
        row = review("Please fix this.")
        row.pop("id")
        with self.assertRaisesRegex(RevenueCloseoutError, "stable positive id"):
            scan_paid_pr(
                item(),
                session=FakeSession(routes(reviews=[row])),
            )


if __name__ == "__main__":
    unittest.main()
