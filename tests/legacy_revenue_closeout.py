from __future__ import annotations

from datetime import datetime, timezone
import unittest

import requests

from concierge.revenue_closeout import (
    RevenueCloseoutError,
    RevenueCloseoutInputError,
    build_closeout_queue,
    scan_paid_pr,
)


SHA = "a" * 40
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
        if isinstance(self.payload, Exception):
            raise self.payload
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
        "currency": "USD",
        "last_seen_at": "2026-09-13T00:00:00Z",
    }
    value.update(overrides)
    return value


def pr_payload(**overrides):
    value = {
        "html_url": PR_URL,
        "state": "open",
        "merged_at": None,
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


class RevenueCloseoutTests(unittest.TestCase):
    def test_open_without_feedback_waits_for_acceptance(self):
        result = scan_paid_pr(item(), session=FakeSession(routes()))
        self.assertEqual(result["next_action"], "await_acceptance")
        self.assertEqual(result["cash_status"], "not_inferred")
        self.assertEqual(result["new_feedback_count"], 0)

    def test_changes_requested_has_highest_closeout_priority(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-13T01:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
                "html_url": PR_URL + "#pullrequestreview-1",
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "repair_requested")
        self.assertEqual(result["reason"], "current_maintainer_changes_requested")
        self.assertEqual(result["latest_feedback"]["author"], "maintainer")

    def test_maintainer_comment_routes_response(self):
        comments = [
            {
                "updated_at": "2026-09-13T02:00:00+00:00",
                "author_association": "OWNER",
                "user": {"login": "owner"},
                "html_url": PR_URL + "#issuecomment-1",
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(comments=comments)))
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(result["new_feedback_count"], 1)

    def test_approved_review_does_not_trigger_reply(self):
        reviews = [
            {
                "state": "APPROVED",
                "submitted_at": "2026-09-13T02:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer", "type": "User"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "await_acceptance")
        self.assertEqual(result["reason"], "new_nonactionable_maintainer_review")
        self.assertEqual(result["new_feedback_count"], 1)

    def test_maintainer_bot_feedback_is_ignored(self):
        comments = [
            {
                "updated_at": "2026-09-13T02:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "review-bot[bot]", "type": "Bot"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(comments=comments)))
        self.assertEqual(result["next_action"], "await_acceptance")
        self.assertEqual(result["new_feedback_count"], 0)

    def test_settlement_followup_url_requires_real_absolute_url(self):
        for bad in ("https://", "http:///missing-host", "javascript:alert(1)", "//example.test/x", ""):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(RevenueCloseoutInputError, "absolute HTTP"):
                    scan_paid_pr(
                        item(settlement_followup_url=bad),
                        session=FakeSession({}),
                    )

    def test_empty_queue_still_validates_max_pages(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "max_pages must be positive"):
            build_closeout_queue([], session=FakeSession({}), max_pages=0)

    def test_external_nonmaintainer_comment_does_not_create_work(self):
        comments = [
            {
                "updated_at": "2026-09-13T02:00:00Z",
                "author_association": "CONTRIBUTOR",
                "user": {"login": "random-person"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(comments=comments)))
        self.assertEqual(result["next_action"], "await_acceptance")

    def test_operator_comment_does_not_create_work(self):
        comments = [
            {
                "updated_at": "2026-09-13T02:00:00Z",
                "author_association": "OWNER",
                "user": {"login": "builder"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(comments=comments)))
        self.assertEqual(result["next_action"], "await_acceptance")

    def test_old_comment_feedback_is_not_replayed(self):
        reviews = [
            {
                "state": "COMMENTED",
                "submitted_at": "2026-09-12T23:59:59Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "await_acceptance")
        self.assertEqual(result["new_feedback_count"], 0)

    def test_old_unresolved_change_request_still_routes_repair(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-12T23:59:59Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            }
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "repair_requested")
        self.assertEqual(result["reason"], "current_maintainer_changes_requested")
        self.assertEqual(result["new_feedback_count"], 0)
        self.assertEqual(result["current_change_request_count"], 1)

    def test_later_approval_clears_same_maintainer_change_request(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-12T22:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            },
            {
                "state": "APPROVED",
                "submitted_at": "2026-09-12T23:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            },
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "await_acceptance")
        self.assertEqual(result["current_change_request_count"], 0)
        self.assertEqual(result["new_feedback_count"], 0)

    def test_comment_review_does_not_clear_change_request(self):
        reviews = [
            {
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-09-12T22:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            },
            {
                "state": "COMMENTED",
                "submitted_at": "2026-09-12T23:00:00Z",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            },
        ]
        result = scan_paid_pr(item(), session=FakeSession(routes(reviews=reviews)))
        self.assertEqual(result["next_action"], "repair_requested")
        self.assertEqual(result["current_change_request_count"], 1)

    def test_merged_pr_routes_missing_settlement_followup_without_cash_claim(self):
        pr = pr_payload(state="closed", merged_at="2026-09-13T03:00:00Z")
        result = scan_paid_pr(item(), session=FakeSession(routes(pr=pr)))
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["cash_status"], "not_inferred")

    def test_settlement_route_metadata_stays_actionable_without_send_evidence(self):
        pr = pr_payload(state="closed", merged_at="2026-09-13T03:00:00Z")
        result = scan_paid_pr(
            item(settlement_followup_url="https://example.test/followup/17"),
            session=FakeSession(routes(pr=pr)),
        )
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["reason"], "merged_route_metadata_without_send_evidence")
        self.assertFalse(result["settlement_route_proves_prior_contact"])

    def test_closed_unmerged_is_fail_closed(self):
        pr = pr_payload(state="closed", merged_at=None)
        result = scan_paid_pr(item(), session=FakeSession(routes(pr=pr)))
        self.assertEqual(result["state"], "CLOSED_UNMERGED")
        self.assertEqual(result["next_action"], "investigate_closed_unmerged")

    def test_expected_head_movement_blocks_without_fetching_feedback(self):
        session = FakeSession(
            {
                "https://api.github.com/repos/acme/widgets/pulls/17": pr_payload(),
            }
        )
        result = scan_paid_pr(
            item(expected_head_sha="b" * 40),
            session=session,
        )
        self.assertEqual(result["state"], "HEAD_MOVED")
        self.assertEqual(result["reason"], "expected_head_moved")
        self.assertEqual(len(session.calls), 1)

    def test_identity_mismatch_fails_closed(self):
        pr = pr_payload(html_url="https://github.com/acme/widgets/pull/18")
        with self.assertRaisesRegex(RevenueCloseoutError, "identity mismatch"):
            scan_paid_pr(item(), session=FakeSession(routes(pr=pr)))

    def test_wrong_author_fails_closed(self):
        pr = pr_payload(user={"login": "somebody-else"})
        with self.assertRaisesRegex(RevenueCloseoutInputError, "not authored"):
            scan_paid_pr(item(), session=FakeSession(routes(pr=pr)))

    def test_malformed_money_and_bool_pr_reject(self):
        for bad in ("NaN", "Infinity", "0", "-1", True, 1.25):
            with self.subTest(bad=bad):
                with self.assertRaises(RevenueCloseoutInputError):
                    scan_paid_pr(
                        item(advertised_amount=bad),
                        session=FakeSession({}),
                    )
        with self.assertRaises(RevenueCloseoutInputError):
            scan_paid_pr(item(pr=True), session=FakeSession({}))

    def test_missing_or_future_last_seen_rejects_before_network(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "last_seen_at"):
            scan_paid_pr(item(last_seen_at=None), session=FakeSession({}))
        with self.assertRaisesRegex(RevenueCloseoutInputError, "must not be in the future"):
            scan_paid_pr(
                item(last_seen_at="2099-01-01T00:00:00Z"),
                session=FakeSession({}),
            )

    def test_dot_segment_repo_rejects_before_network(self):
        for repo in ("../widgets", "acme/.."):
            with self.subTest(repo=repo):
                with self.assertRaises(RevenueCloseoutInputError):
                    scan_paid_pr(item(repo=repo), session=FakeSession({}))

    def test_same_action_mixed_currency_preserves_manifest_order(self):
        base = "https://api.github.com/repos"
        session_routes = {}
        inputs = []
        for repo, number, amount, currency in (
            ("alpha/one", 1, "1", "USD"),
            ("beta/two", 2, "999999", "RTC"),
        ):
            session_routes[f"{base}/{repo}/pulls/{number}"] = {
                "html_url": f"https://github.com/{repo}/pull/{number}",
                "state": "open",
                "merged_at": None,
                "user": {"login": "builder"},
                "head": {"sha": SHA},
            }
            session_routes[(f"{base}/{repo}/pulls/{number}/reviews", (("page", 1), ("per_page", 100)))] = []
            session_routes[(f"{base}/{repo}/issues/{number}/comments", (("page", 1), ("per_page", 100)))] = []
            inputs.append({
                "repo": repo,
                "pr": number,
                "operator_login": "builder",
                "advertised_amount": amount,
                "currency": currency,
                "last_seen_at": "2026-09-13T00:00:00Z",
            })
        result = build_closeout_queue(inputs, session=FakeSession(session_routes))
        self.assertEqual([row["repo"] for row in result], ["alpha/one", "beta/two"])

    def test_naive_last_seen_rejects(self):
        with self.assertRaisesRegex(RevenueCloseoutInputError, "include a timezone"):
            scan_paid_pr(
                item(last_seen_at="2026-09-13T01:00:00"),
                session=FakeSession({}),
            )

    def test_duplicate_items_reject_before_second_scan(self):
        session = FakeSession(routes())
        with self.assertRaisesRegex(RevenueCloseoutInputError, "duplicate closeout item"):
            build_closeout_queue([item(), item()], session=session)
        self.assertEqual(len(session.calls), 3)

    def test_queue_prioritizes_action_without_cross_currency_value_claims(self):
        base = "https://api.github.com/repos"
        session_routes = {}
        inputs = []
        cases = [
            ("alpha/one", 1, "25", "open", None, [], []),
            (
                "beta/two",
                2,
                "10",
                "open",
                None,
                [
                    {
                        "state": "CHANGES_REQUESTED",
                        "submitted_at": "2026-09-13T01:00:00Z",
                        "author_association": "OWNER",
                        "user": {"login": "maint"},
                    }
                ],
                [],
            ),
            ("gamma/three", 3, "500", "closed", "2026-09-13T02:00:00Z", [], []),
        ]
        for repo, number, amount, state, merged_at, reviews, comments in cases:
            url = f"https://github.com/{repo}/pull/{number}"
            session_routes[f"{base}/{repo}/pulls/{number}"] = {
                "html_url": url,
                "state": state,
                "merged_at": merged_at,
                "user": {"login": "builder"},
                "head": {"sha": SHA},
            }
            session_routes[
                (
                    f"{base}/{repo}/pulls/{number}/reviews",
                    (("page", 1), ("per_page", 100)),
                )
            ] = reviews
            session_routes[
                (
                    f"{base}/{repo}/issues/{number}/comments",
                    (("page", 1), ("per_page", 100)),
                )
            ] = comments
            inputs.append(
                {
                    "repo": repo,
                    "pr": number,
                    "operator_login": "builder",
                    "advertised_amount": amount,
                    "currency": "USD",
                    "last_seen_at": "2026-09-13T00:00:00Z",
                }
            )
        result = build_closeout_queue(inputs, session=FakeSession(session_routes))
        self.assertEqual(
            [(r["repo"], r["next_action"]) for r in result],
            [
                ("beta/two", "repair_requested"),
                ("gamma/three", "route_settlement_followup"),
                ("alpha/one", "await_acceptance"),
            ],
        )

    def test_malformed_feedback_payload_fails_closed(self):
        bad = routes(reviews={"not": "a list"})
        with self.assertRaisesRegex(RevenueCloseoutError, "review response was not a list"):
            scan_paid_pr(item(), session=FakeSession(bad))

    def test_max_pages_exhaustion_fails_closed(self):
        base = "https://api.github.com/repos/acme/widgets"
        session = FakeSession(
            {
                f"{base}/pulls/17": pr_payload(),
                (
                    f"{base}/pulls/17/reviews",
                    (("page", 1), ("per_page", 100)),
                ): [
                    {
                        "state": "COMMENTED",
                        "submitted_at": "2026-09-13T01:00:00Z",
                        "author_association": "CONTRIBUTOR",
                        "user": {"login": f"u{i}"},
                    }
                    for i in range(100)
                ],
            }
        )
        with self.assertRaisesRegex(RevenueCloseoutError, "pagination exceeded"):
            scan_paid_pr(item(), session=session, max_pages=1)


if __name__ == "__main__":
    unittest.main()
