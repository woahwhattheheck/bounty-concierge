# SPDX-License-Identifier: MIT
"""Focused offline admission behavior when GitHub reports a moved repository."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from concierge.bounty_live_cash_admission import (
    LiveCashAdmissionError, _read_repository_snapshot, evaluate_live_cash_admission,
)

OLD, NEW = "prior-owner/sample", "canonical-owner/sample"
ISSUE = {"title": "[Bounty: $75] Example", "body": "", "labels": []}
PREFLIGHT = {
    "qualification": {
        "dispatch": True, "disposition": "ALLOW", "reason_codes": [],
        "signals": {
            "advertised_reward_usd": ["75"], "live_label_reward_usd": [],
            "advertised_reward_rtc": [], "live_label_reward_rtc": [],
        },
    },
    "canonical_audit": {},
}

class RepositoryTransferGateTest(unittest.TestCase):
    def evaluate(self, *, archived=False, canonical=NEW):
        with (
            patch("concierge.bounty_live_cash_admission._read_issue_generation",
                  return_value=(ISSUE, ("stable",))),
            patch("concierge.bounty_live_cash_admission.bp.preflight_bounty",
                  return_value=PREFLIGHT),
            patch("concierge.bounty_live_cash_admission.bp._get_json",
                  return_value={"full_name": canonical, "archived": archived, "fork": False}),
        ):
            return evaluate_live_cash_admission(OLD, 21, session=object())

    def test_transfer_is_hold_not_admitted_reward(self):
        receipt = self.evaluate()
        self.assertEqual(receipt["disposition"], "HOLD_CANONICAL_REPOSITORY_RELOCATED")
        self.assertIsNone(receipt["route"])
        self.assertEqual(receipt["identity"]["repo"], OLD)
        self.assertEqual(receipt["source"]["repository"]["full_name"], NEW)
        self.assertEqual(receipt["source"]["repository"]["relocated_from"], OLD)
        self.assertIn("CANONICAL_REPOSITORY_AND_ISSUE_RECHECK_REQUIRED", receipt["reason_codes"])

    def test_canonical_destination_archived_is_still_rejected(self):
        receipt = self.evaluate(archived=True)
        self.assertEqual(receipt["disposition"], "REJECT_CANONICAL_REPOSITORY_ARCHIVED")
        self.assertIsNone(receipt["route"])

    def test_bad_full_name_is_never_accepted_as_canonical(self):
        with patch("concierge.bounty_live_cash_admission.bp._get_json",
                   return_value={"full_name": "not a repository path",
                                 "archived": False, "fork": False}):
            with self.assertRaisesRegex(LiveCashAdmissionError, "malformed"):
                _read_repository_snapshot(OLD, None, session=object())

if __name__ == "__main__":
    unittest.main()
