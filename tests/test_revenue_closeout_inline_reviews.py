from __future__ import annotations

import unittest

import requests

from concierge.revenue_closeout import RevenueCloseoutError, scan_paid_pr


SHA = "a" * 40
BASE = "https://api.github.com/repos/acme/widgets"
PR_URL = "https://github.com/acme/widgets/pull/17"


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

    def get(self, url, *, headers=None, params=None, timeout=None):
        key = (url, None if params is None else tuple(sorted(params.items())))
        if key not in self.routes:
            raise AssertionError(f"unexpected GET {url} params={params}")
        return FakeResponse(self.routes[key])


def item(**overrides):
    value = {
        "repo": "acme/widgets",
        "pr": 17,
        "operator_login": "builder",
        "advertised_amount": "250",
        "currency": "USD",
        "last_seen_at": "2026-09-13T00:00:00Z",
    }
    value.update(overrides)
    return value


def pr_payload(*, merged=False):
    return {
        "html_url": PR_URL,
        "state": "closed" if merged else "open",
        "merged_at": "2026-09-13T03:00:00Z" if merged else None,
        "user": {"login": "builder"},
        "head": {"sha": SHA},
    }


def key(url, page=1):
    return (url, (("page", page), ("per_page", 100)))


def session_routes(*, pr=None, reviews=None, inline=None, comments=None):
    return {
        (f"{BASE}/pulls/17", None): pr or pr_payload(),
        key(f"{BASE}/pulls/17/reviews"): reviews or [],
        key(f"{BASE}/pulls/17/comments"): inline or [],
        key(f"{BASE}/issues/17/comments"): comments or [],
    }


def inline_comment(
    *,
    comment_id=22,
    review_id=11,
    at="2026-09-13T02:00:00Z",
    association="MEMBER",
    login="maintainer",
):
    return {
        "id": comment_id,
        "pull_request_review_id": review_id,
        "updated_at": at,
        "created_at": at,
        "author_association": association,
        "user": {"login": login, "type": "User"},
        "html_url": PR_URL + f"#discussion_r{comment_id}",
    }


class InlineReviewCloseoutTests(unittest.TestCase):
    def test_merged_pr_with_fresh_inline_maintainer_feedback_routes_response(self):
        session = FakeSession(
            session_routes(pr=pr_payload(merged=True), inline=[inline_comment()])
        )
        result = scan_paid_pr(item(), session=session)
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(result["reason"], "new_maintainer_feedback")
        self.assertEqual(result["new_feedback_count"], 1)
        self.assertEqual(result["latest_feedback"]["kind"], "inline_comment")

    def test_inline_child_suppresses_overlapping_commented_parent_review(self):
        review = {
            "id": 11,
            "state": "COMMENTED",
            "submitted_at": "2026-09-13T02:00:00Z",
            "author_association": "MEMBER",
            "user": {"login": "maintainer", "type": "User"},
            "html_url": PR_URL + "#pullrequestreview-11",
        }
        result = scan_paid_pr(
            item(),
            session=FakeSession(
                session_routes(reviews=[review], inline=[inline_comment()])
            ),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(result["new_feedback_count"], 1)
        self.assertEqual(result["latest_feedback"]["kind"], "inline_comment")

    def test_identical_inline_comment_replayed_across_pages_is_deduplicated(self):
        first = inline_comment()
        fillers = [
            inline_comment(
                comment_id=1000 + index,
                review_id=2000 + index,
                association="CONTRIBUTOR",
                login=f"contributor-{index}",
            )
            for index in range(99)
        ]
        routes = session_routes()
        routes[key(f"{BASE}/pulls/17/comments")] = [first, *fillers]
        routes[key(f"{BASE}/pulls/17/comments", 2)] = [dict(first)]
        result = scan_paid_pr(item(), session=FakeSession(routes), max_pages=2)
        self.assertEqual(result["new_feedback_count"], 1)
        self.assertEqual(result["next_action"], "respond_to_maintainer")

    def test_conflicting_duplicate_inline_id_fails_closed(self):
        first = inline_comment()
        fillers = [
            inline_comment(
                comment_id=1000 + index,
                review_id=2000 + index,
                association="CONTRIBUTOR",
                login=f"contributor-{index}",
            )
            for index in range(99)
        ]
        changed = inline_comment(at="2026-09-13T02:01:00Z")
        routes = session_routes()
        routes[key(f"{BASE}/pulls/17/comments")] = [first, *fillers]
        routes[key(f"{BASE}/pulls/17/comments", 2)] = [changed]
        with self.assertRaisesRegex(RevenueCloseoutError, "duplicate id changed"):
            scan_paid_pr(item(), session=FakeSession(routes), max_pages=2)

    def test_malformed_inline_comment_identity_fails_closed(self):
        malformed = inline_comment()
        malformed.pop("id")
        with self.assertRaisesRegex(RevenueCloseoutError, "inline_comment omitted id"):
            scan_paid_pr(
                item(),
                session=FakeSession(session_routes(inline=[malformed])),
            )

    def test_inline_pagination_exhaustion_fails_closed(self):
        full_page = [
            inline_comment(
                comment_id=1000 + index,
                review_id=2000 + index,
                association="CONTRIBUTOR",
                login=f"contributor-{index}",
            )
            for index in range(100)
        ]
        routes = session_routes(inline=full_page)
        with self.assertRaisesRegex(RevenueCloseoutError, "pagination exceeded"):
            scan_paid_pr(item(), session=FakeSession(routes), max_pages=1)


if __name__ == "__main__":
    unittest.main()
