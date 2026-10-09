# SPDX-License-Identifier: MIT
"""Focused regression: conditional campaign terms must not route as fixed cash."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.bounty_live_cash_admission import evaluate_live_cash_admission


REPO = "example/repo"
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


class DiscretionaryCashGateTest(unittest.TestCase):
    def evaluate(self, *, body="", labels=None):
        issue = {
            "title": "[BOUNTY $75] Add session refresh handler",
            "body": body,
            "labels": labels if labels is not None else [],
        }
        with (
            patch(
                "concierge.bounty_live_cash_admission._read_issue_generation",
                return_value=(issue, MARKER),
            ),
            patch(
                "concierge.bounty_live_cash_admission.bp.preflight_bounty",
                return_value=PREFLIGHT,
            ),
            patch(
                "concierge.bounty_live_cash_admission._read_repository_snapshot",
                return_value={
                    "full_name": REPO, "archived": False, "fork": False
                },
            ) as read_repository,
        ):
            receipt = evaluate_live_cash_admission(REPO, 18, session=object())
        return receipt, read_repository

    def test_maybe_rewarded_separate_label_blocks_active_cash(self):
        receipt, read_repository = self.evaluate(
            labels=[{"name": "Maybe Rewarded"}]
        )
        self.assertEqual(receipt["disposition"], "HOLD_NON_FIXED_USD_REWARD")
        self.assertIsNone(receipt["route"])
        self.assertFalse(receipt["economics"]["fixed_semantics"])
        self.assertIn(
            "USD_REWARD_NOT_FIXED_GUARANTEED_AMOUNT", receipt["reason_codes"]
        )
        read_repository.assert_not_called()

    def test_conditional_sentence_on_separate_line_blocks_active_cash(self):
        receipt, read_repository = self.evaluate(
            body=(
                "Source acceptance is not an award.\n"
                "Passing PRs may qualify for a reward after GrantFox approval."
            )
        )
        self.assertEqual(receipt["disposition"], "HOLD_NON_FIXED_USD_REWARD")
        self.assertFalse(receipt["economics"]["fixed_semantics"])
        read_repository.assert_not_called()

    def test_normal_fixed_payout_preserves_live_cash_routing(self):
        receipt, read_repository = self.evaluate(
            body="Fixed $75 bounty. Payment on acceptance.",
            labels=[{"name": "bounty"}],
        )
        self.assertEqual(receipt["disposition"], "ACTIVE_REVIEW")
        self.assertEqual(receipt["route"], "main_bounty_queue")
        self.assertTrue(receipt["economics"]["fixed_semantics"])
        read_repository.assert_called_once()


if __name__ == "__main__":
    unittest.main()
