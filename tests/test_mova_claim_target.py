# SPDX-License-Identifier: MIT
"""Focused original-revenue-attribution regression for MOVA claims."""
import unittest

from concierge.mova_claim_target import ClaimTargetMismatch, validate_claim_target


class ExactIssueClaimTests(unittest.TestCase):
    def test_exact_issue_command_is_preserved(self):
        validate_claim_target("/claim #702\nI request the bounty payment.", 702)
        validate_claim_target("/claim 702", 702)

    def test_wrong_issue_command_fails_closed(self):
        with self.assertRaises(ClaimTargetMismatch):
            validate_claim_target("/claim #999\nI request the $50 reward.", 702)

    def test_near_prefix_not_accepted(self):
        with self.assertRaises(ClaimTargetMismatch):
            validate_claim_target("/claim #7029", 702)

    def test_all_commands_must_agree(self):
        with self.assertRaises(ClaimTargetMismatch):
            validate_claim_target("/claim #702\n/claim #703", 702)

    def test_malformed_target_fails_closed(self):
        for text in ("/claim #", "/claim #702x", "/claim #0"):
            with self.subTest(text=text), self.assertRaises(ClaimTargetMismatch):
                validate_claim_target(text, 702)

    def test_legacy_forms_remain_valid(self):
        validate_claim_target("/claim", 702)
        validate_claim_target("I request eligible bounty compensation for #702.", 702)


if __name__ == "__main__":
    unittest.main()
