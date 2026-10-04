"""Focused source-context checks; run directly with Python's standard library."""
import importlib.util
import os
from pathlib import Path
import unittest

SOURCE = Path(os.environ.get(
    "REWARD_EVIDENCE_SOURCE",
    str(Path(__file__).resolve().parents[1] / "concierge" / "reward_evidence.py"),
))
spec = importlib.util.spec_from_file_location("reward_evidence_under_test", SOURCE)
reward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reward)


class RewardSourceCaveatsTest(unittest.TestCase):
    def test_closure_far_from_first_amount_remains_visible(self):
        evidence = reward.extract_reward_evidence(
            "$500 reward", "Implementation details. " * 80 +
            "This bounty program is closed and is not accepting new claims.",
        )
        self.assertEqual(evidence["status"], "unconfirmed_text")
        self.assertEqual(evidence["exact_text"], "$500")
        self.assertNotIn("closed", evidence["excerpt"])
        self.assertIn("closed", reward.reward_context({"reward_evidence": evidence}))
        self.assertEqual(evidence["source_caveats"][0]["kind"], "closed")
        self.assertEqual(evidence["source_caveats"][0]["source"], "body")

    def test_proposed_compensation_is_context_not_an_admission_gate(self):
        evidence = reward.extract_reward_evidence(
            "50 RTC", "Technical details. " * 80 +
            "This is a proposal, not an approved bounty.",
        )
        row = {"reward_evidence": evidence}
        self.assertEqual(reward.reward_filter_value(row), 50.0)
        self.assertEqual(reward.reward_sort_key(row), (True, 50.0))
        self.assertEqual(reward.reward_summary(row), "unconfirmed 50 RTC")
        self.assertIn("not an approved bounty", reward.reward_context(row))
        self.assertEqual(evidence["source_caveats"][0]["kind"], "proposal")

    def test_ordinary_offer_and_negated_closure_keep_existing_shape(self):
        evidence = reward.extract_reward_evidence(
            "$25 fix", "This bounty is not closed. Paid on acceptance.",
        )
        self.assertEqual(evidence, {
            "status": "unconfirmed_text", "amount_rtc": None,
            "exact_text": "$25", "excerpt": "$25 fix", "mentions": [],
            "evidence_kind": "unconfirmed_text",
        })
        self.assertEqual(reward.reward_summary({"reward_evidence": evidence}),
                         "unconfirmed $25")
        self.assertEqual(reward.reward_context({}),
                         "No retained reward excerpt; legacy numeric data is not payout evidence.")

    def test_historical_context_and_stored_caveats_stay_bounded(self):
        evidence = reward.extract_reward_evidence(
            "20 RTC", "Old campaign is closed. Current work pays 20 RTC. " +
            "Submissions are withdrawn. " + "x" * 200 +
            " Proposed milestone. " + "y" * 200 + " Applications are closed.",
        )
        row = {"reward_evidence": evidence}
        self.assertEqual(reward.reward_filter_value(row), 20.0)
        caveats = evidence["source_caveats"]
        self.assertEqual(len(caveats), 3)
        self.assertTrue(all(len(item["excerpt"]) <= 120 for item in caveats))
        self.assertIn("Source caveat (unverified):", reward.reward_context(row))
        evidence["source_caveats"] = [None, {"excerpt": 17},
                                      {"excerpt": "\x00\n" + "z" * 10000}]
        context = reward.reward_context(row)
        rendered = context.split("Source caveat (unverified): ")[1]
        self.assertLessEqual(len(rendered), 120)
        self.assertNotIn("\n", context)
        self.assertNotIn("\x00", context)
        evidence["source_caveats"] = "malformed"
        self.assertNotIn("Source caveat", reward.reward_context(row))


if __name__ == "__main__":
    unittest.main(verbosity=2)
