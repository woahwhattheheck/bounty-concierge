# SPDX-License-Identifier: MIT
"""Focused sponsor-assignment and cash-admission checks (synthetic receipts)."""
from datetime import datetime, timezone
from unittest import TestCase

from concierge.three_mdeb_admission import classify_issue

NOW = datetime(2026, 10, 9, 8, 33, tzinfo=timezone.utc)
ACTOR = "woahwhattheheck"
# Synthetic URLs validate receipt shape only; not proof of a real award.
HISTORY = [{
    "sponsor": "3mdeb",
    "merged": True,
    "paid": True,
    "paid_at": "2026-04-15T10:00:00Z",
    "merged_pr_url": "https://github.com/Dasharo/dasharo-issues/pull/999999",
    "expense_url": "https://opencollective.com/3mdeb_com/expenses/999999",
}]


def issue():
    return {
        "repository": "Dasharo/dasharo-issues",
        "number": 1182,
        "state": "open",
        "labels": ["bounty", {"name": "bounty-medium"}],
        "assignees": [],
        "open_prs": [],
        "last_maintainer_action_at": "2026-10-09T08:30:00Z",
        "offer": {
            "amount": "125",
            "currency": "USD",
            "confirmed_by_sponsor": True,
            "source_url": "https://github.com/Dasharo/dasharo-issues/issues/1182",
        },
    }


def status(snapshot, history=HISTORY):
    return classify_issue(
        snapshot, paid_merge_history=history, actor=ACTOR, as_of=NOW
    )


class ThreeMdebAssignmentAdmissionTest(TestCase):
    def test_unassigned_is_not_build_ready(self):
        result = status(issue())
        self.assertEqual("REQUEST_ASSIGNMENT", result["status"])
        self.assertEqual("NO_CLAIM_EMITTED", result["claim_status"])
        self.assertEqual("NOT_VERIFIED", result["payout_status"])

    def test_actual_assignee_needs_explicit_comment_receipt(self):
        snapshot = issue()
        snapshot["assignees"] = [{"login": ACTOR}]
        self.assertIn("MAINTAINER_ASSIGNMENT_PROOF_MISSING", status(snapshot)["reasons"])
        snapshot["maintainer_assignment_proof"] = {
            "actor": ACTOR,
            "approved": True,
            "comment_url": (
                "https://github.com/Dasharo/dasharo-issues/issues/1182"
                "#issuecomment-123456789"
            ),
        }
        self.assertEqual("BUILD_READY", status(snapshot)["status"])

    def test_bad_labels_or_non_original_assignee_hold(self):
        snapshot = issue()
        snapshot["labels"] = ["bounty", "bouty-warmup"]
        self.assertIn("EXACT_CATEGORY_LABEL_REQUIRED", status(snapshot)["reasons"])
        snapshot = issue()
        snapshot["assignees"] = [{"login": "another-dev"}]
        self.assertIn("ASSIGNED_TO_ANOTHER_CONTRIBUTOR", status(snapshot)["reasons"])

    def test_missing_collision_or_payer_proof_holds(self):
        snapshot = issue()
        del snapshot["open_prs"]
        self.assertIn("OPEN_PR_SEARCH_UNVERIFIED", status(snapshot)["reasons"])
        self.assertIn("SAME_SPONSOR_PAID_MERGE_UNVERIFIED", status(issue(), [])["reasons"])
        snapshot = issue()
        snapshot["open_prs"] = [{"state": "open", "user": {"login": ACTOR}}]
        self.assertIn("ORIGINAL_CARRIER_ALREADY_EXISTS", status(snapshot)["reasons"])

    def test_stale_maintainer_and_missing_hardware_hold(self):
        snapshot = issue()
        snapshot["last_maintainer_action_at"] = "2025-12-01T00:00:00Z"
        self.assertIn("CURRENT_MAINTAINER_RESPONSE_UNVERIFIED", status(snapshot)["reasons"])
        snapshot = issue()
        snapshot["hardware_required"] = True
        self.assertIn("HARDWARE_VALIDATION_NOT_READY", status(snapshot)["reasons"])
