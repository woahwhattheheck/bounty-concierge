# SPDX-License-Identifier: MIT
"""Focused archived/fork repository admission regression; no provider traffic."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.bounty_live_cash_admission import (
    LiveCashAdmissionError,
    _read_repository_snapshot,
    evaluate_live_cash_admission,
)

REPO = "example/repo"
SNAPSHOT = {"full_name": REPO, "archived": False, "fork": False}
ISSUE = {"title": "[Bounty: $75] Example issue", "body": "", "labels": []}
MARKER = ("canonical-issue-generation",)
PREFLIGHT = {
    "qualification": {
        "dispatch": True,
        "disposition": "ALLOW",
        "reason_codes": [],
        "signals": {
            "advertised_reward_usd": ["75"],
            "live_label_reward_usd": [],
            "advertised_reward_rtc": [],
            "live_label_reward_rtc": [],
        },
    },
    "canonical_audit": {},
}


class RepositoryAdmissionGateTest(unittest.TestCase):
    def evaluate(self, *, archived=False, fork=False):
        snapshot = {**SNAPSHOT, "archived": archived, "fork": fork}
        with (
            patch(
                "concierge.bounty_live_cash_admission._read_issue_generation",
                return_value=(ISSUE, MARKER),
            ),
            patch(
                "concierge.bounty_live_cash_admission.bp.preflight_bounty",
                return_value=PREFLIGHT,
            ),
            patch(
                "concierge.bounty_live_cash_admission._read_repository_snapshot",
                return_value=snapshot,
            ) as read_repo,
        ):
            receipt = evaluate_live_cash_admission(REPO, 92, session=object())
        read_repo.assert_called_once()
        return receipt

    def test_archived_repository_pruned_before_work_lease(self):
        receipt = self.evaluate(archived=True)
        self.assertEqual(receipt["disposition"], "REJECT_CANONICAL_REPOSITORY_ARCHIVED")
        self.assertIsNone(receipt["route"])
        self.assertIn("CANONICAL_REPOSITORY_ARCHIVED", receipt["reason_codes"])
        self.assertTrue(receipt["source"]["repository"]["archived"])

    def test_forked_repository_held_for_payability_review(self):
        receipt = self.evaluate(fork=True)
        self.assertEqual(receipt["disposition"], "HOLD_CANONICAL_REPOSITORY_FORK")
        self.assertIsNone(receipt["route"])
        self.assertIn("CANONICAL_REPOSITORY_FORK_REVIEW_REQUIRED", receipt["reason_codes"])

    def test_active_original_repository_keeps_existing_cash_route(self):
        receipt = self.evaluate()
        self.assertEqual(receipt["disposition"], "ACTIVE_REVIEW")
        self.assertEqual(receipt["route"], "main_bounty_queue")
        self.assertEqual(receipt["economics"]["fixed_amount"], "75")
        self.assertEqual(receipt["source"]["repository"], SNAPSHOT)

    def test_malformed_repository_snapshot_fails_closed(self):
        with patch(
            "concierge.bounty_live_cash_admission.bp._get_json",
            return_value={"full_name": REPO, "archived": True, "fork": "no"},
        ):
            with self.assertRaisesRegex(
                LiveCashAdmissionError, "repository snapshot was malformed"
            ):
                _read_repository_snapshot(REPO, None, session=object())


if __name__ == "__main__":
    unittest.main()
