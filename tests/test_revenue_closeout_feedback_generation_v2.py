from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
        # One second newer than the mutable-body timestamp. Timestamp-only
        # closeout logic would therefore incorrectly treat the edit as handled.
        "last_seen_at": "2026-09-13T01:00:01Z",
    }
    value.update(overrides)
    return value


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


def inline(body: str, *, feedback_id: int = 700):
    return {
        "id": feedback_id,
        "pull_request_review_id": 501,
        "body": body,
        "updated_at": "2026-09-13T01:00:00Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer", "type": "User"},
        "html_url": PR_URL + f"#discussion_r{feedback_id}",
    }


def issue_comment(body: str, *, feedback_id: int = 800):
    return {
        "id": feedback_id,
        "body": body,
        "updated_at": "2026-09-13T01:00:00Z",
        "author_association": "MEMBER",
        "user": {"login": "maintainer", "type": "User"},
        "html_url": PR_URL + f"#issuecomment-{feedback_id}",
    }


def body_ack(kind: str, feedback_id: int, body: str):
    return {
        "kind": kind,
        "feedback_id": feedback_id,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


class RevenueCloseoutFeedbackGenerationV2Tests(unittest.TestCase):
    def test_old_inline_body_generation_blocks_settlement(self):
        body = "Please change this line."
        result = scan_paid_pr(
            item(),
            session=FakeSession(routes(inline_comments=[inline(body)])),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(
            result["reason"],
            "unacknowledged_maintainer_feedback_body_generation",
        )
        self.assertEqual(result["new_feedback_count"], 0)
        self.assertEqual(result["unacknowledged_feedback_body_count"], 1)
        receipt = result["unacknowledged_feedback_body_generations"][0]
        self.assertEqual(receipt["kind"], "inline_comment")
        self.assertEqual(receipt["feedback_id"], 700)
        self.assertEqual(
            receipt["body_sha256"],
            body_ack("inline_comment", 700, body)["body_sha256"],
        )

    def test_exact_inline_generation_ack_allows_settlement_route(self):
        body = "Please change this line."
        result = scan_paid_pr(
            item(
                acknowledged_feedback_bodies=[
                    body_ack("inline_comment", 700, body)
                ]
            ),
            session=FakeSession(routes(inline_comments=[inline(body)])),
        )
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["reason"], "merged_without_settlement_route")
        self.assertEqual(result["acknowledged_feedback_body_count"], 1)
        self.assertEqual(result["unacknowledged_feedback_body_count"], 0)

    def test_inline_same_id_body_edit_reopens_old_timestamp_generation(self):
        old_body = "Looks good."
        edited = "Looks good. Please add rollback coverage."
        result = scan_paid_pr(
            item(
                acknowledged_feedback_bodies=[
                    body_ack("inline_comment", 700, old_body)
                ]
            ),
            session=FakeSession(routes(inline_comments=[inline(edited)])),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(
            result["reason"],
            "unacknowledged_maintainer_feedback_body_generation",
        )
        receipt = result["unacknowledged_feedback_body_generations"][0]
        self.assertEqual(
            receipt["body_sha256"],
            body_ack("inline_comment", 700, edited)["body_sha256"],
        )
        self.assertNotEqual(
            receipt["body_sha256"],
            body_ack("inline_comment", 700, old_body)["body_sha256"],
        )

    def test_old_issue_comment_body_generation_blocks_settlement(self):
        body = "Please document the failure mode."
        result = scan_paid_pr(
            item(),
            session=FakeSession(routes(issue_comments=[issue_comment(body)])),
        )
        self.assertEqual(result["next_action"], "respond_to_maintainer")
        self.assertEqual(
            result["reason"],
            "unacknowledged_maintainer_feedback_body_generation",
        )
        receipt = result["unacknowledged_feedback_body_generations"][0]
        self.assertEqual(receipt["kind"], "comment")
        self.assertEqual(receipt["feedback_id"], 800)

    def test_exact_issue_comment_generation_ack_allows_settlement_route(self):
        body = "Please document the failure mode."
        result = scan_paid_pr(
            item(
                acknowledged_feedback_bodies=[body_ack("comment", 800, body)]
            ),
            session=FakeSession(routes(issue_comments=[issue_comment(body)])),
        )
        self.assertEqual(result["next_action"], "route_settlement_followup")
        self.assertEqual(result["unacknowledged_feedback_body_count"], 0)

    def test_body_bearing_issue_comment_requires_stable_positive_id(self):
        row = issue_comment("Please fix this.")
        row.pop("id")
        with self.assertRaisesRegex(RevenueCloseoutError, "stable positive id"):
            scan_paid_pr(
                item(),
                session=FakeSession(routes(issue_comments=[row])),
            )

    def test_malformed_generalized_ack_rejects_before_network(self):
        good_digest = "b" * 64
        bad_values = [
            "not-a-list",
            [{"kind": "comment", "feedback_id": 800}],
            [{"kind": "unknown", "feedback_id": 800, "body_sha256": good_digest}],
            [{"kind": "comment", "feedback_id": True, "body_sha256": good_digest}],
            [{"kind": "comment", "feedback_id": 800, "body_sha256": "B" * 64}],
            [
                {"kind": "comment", "feedback_id": 800, "body_sha256": good_digest},
                {"kind": "comment", "feedback_id": 800, "body_sha256": good_digest},
            ],
        ]
        for bad in bad_values:
            with self.subTest(bad=bad):
                session = FakeSession({})
                with self.assertRaises(RevenueCloseoutInputError):
                    scan_paid_pr(
                        item(acknowledged_feedback_bodies=bad),
                        session=session,
                    )
                self.assertEqual(session.calls, [])

    def test_direct_core_cli_is_mechanically_non_authoritative(self):
        manifest = {
            "schema_version": 1,
            "items": [item()],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "concierge.revenue_closeout_core",
                    str(path),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("non-authoritative implementation surface", proc.stderr)


if __name__ == "__main__":
    unittest.main()
