# SPDX-License-Identifier: MIT
"""Focused coverage for the post-PR968 claim waiver and cash amount gaps."""
import unittest
from concierge.bounty_claim_text import audit_bounty_claim_text

class CompensationCopyExpansionTest(unittest.TestCase):
    def test_affirmative_cash_amount(self):
        self.assertEqual((),audit_bounty_claim_text("I affirmatively request the advertised $30 Algora payment."))
        self.assertEqual((),audit_bounty_claim_text("I request £100 bounty."))

    def test_missed_waivers_fail_closed(self):
        for phrase in (
            "I do not claim the reward.",
            "This isn't a claim.",
            "No compensation expected.",
            "I'm not claiming; I am requesting the reward.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn("BOUNTY_COMPENSATION_WAIVER", audit_bounty_claim_text(phrase))

    def test_existing_compensation_request_still_accepted(self):
        self.assertEqual((), audit_bounty_claim_text("I am affirmatively requesting the bounty."))

if __name__ == "__main__":
    unittest.main()
