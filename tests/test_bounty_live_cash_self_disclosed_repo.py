# SPDX-License-Identifier: MIT
"""One source-bound admission regression: explicit test target never leases paid work."""
import unittest
from unittest.mock import patch

from concierge.bounty_live_cash_admission import evaluate_live_cash_admission

REPO = "ApexOpsStudio/ai-gitops-test-target"
ISSUE = {"title": "[Bounty: $50] Add --json", "body": "/bounty 50", "labels": []}
PREFLIGHT = {
    "qualification": {
        "dispatch": True, "disposition": "ALLOW", "reason_codes": [],
        "signals": {
            "advertised_reward_usd": ["50"], "live_label_reward_usd": [],
            "advertised_reward_rtc": [], "live_label_reward_rtc": [],
        },
    },
    "canonical_audit": {},
}


class TestSelfDisclosedCashRouting(unittest.TestCase):
    def test_fake_target_never_routes_even_if_issue_appears_funded(self):
        with (
            patch("concierge.bounty_live_cash_admission._read_issue_generation",
                  return_value=(ISSUE, ("generation",))),
            patch("concierge.bounty_live_cash_admission.bp.preflight_bounty",
                  return_value=PREFLIGHT),
            patch("concierge.bounty_live_cash_admission._read_repository_snapshot",
                  return_value={"full_name": REPO, "archived": False, "fork": False}),
        ):
            receipt = evaluate_live_cash_admission(REPO, 1, session=object())
        self.assertEqual(receipt["disposition"], "REJECT_NONPAYABLE_TEST_REPOSITORY")
        self.assertIsNone(receipt["route"])
        self.assertIn("SPONSOR_SELF_DISCLOSED_TEST_ONLY", receipt["reason_codes"])
        self.assertEqual(receipt["source"]["self_disclosure"]["evidence"]["git_blob_sha"],
                         "91da9a7d29915af0508a0a90e29cff05760b9856")


if __name__ == "__main__":
    unittest.main()
