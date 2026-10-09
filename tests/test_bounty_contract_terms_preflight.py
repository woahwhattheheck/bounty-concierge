# SPDX-License-Identifier: MIT
"""Focused contract-risk examples from source-verified repo and provider statements."""
import unittest
from concierge.bounty_contract_terms_preflight import InvalidPacket, evaluate


class ContractTermsTests(unittest.TestCase):
    def packet(self, **kw):
        return {"repository_full_name": "owner/repo", "issue_number": 14,
                "issue_body": "Normal issue without restrictive contract clauses.", **kw}

    def test_ultimateai_internal_competition(self):
        p = self.packet()
        p["issue_body"] = ("Binding Open-Source Contribution Clause. Internal Development Parallelism: "
                           "our internal core development team works simultaneously. Right to Terminate: "
                           "if internal team deploys before any external community PR is formally merged, "
                           "the bounty may be closed without notice.")
        v = evaluate(p)
        self.assertEqual(v["decision"], "HOLD_CONTRACT_RISK")
        self.assertEqual(v["flags"][0]["code"], "UNILATERAL_INTERNAL_RACE")
        self.assertFalse(v["authorization_to_build"])

    def test_mova_application_precondition(self):
        p = self.packet()
        p["contributor_terms"] = ("Apply for the corresponding bounty on GrantFox first. "
                                  "Don't start implementation before your application is acknowledged.")
        self.assertIn("PREWORK_APPLICATION_ACK", {f["code"] for f in evaluate(p)["flags"]})

    def test_claude_builders_automatic_payment_needs_independent_confirmation(self):
        p = self.packet()
        p["issue_body"] = "Powered by Opire. Payment is released automatically on merge."
        r = evaluate(p)
        self.assertEqual(r["decision"], "HOLD_PROVIDER_PROOF")
        self.assertNotIn("fraud", repr(r).casefold())

    def test_explicit_mock_money_is_excluded(self):
        p = self.packet()
        p["listing_text"] = "Mock-payments mode: no real funds move during MVP."
        self.assertEqual(evaluate(p)["decision"], "EXCLUDE_NONCASH")

    def test_clean_issue_still_not_authorized(self):
        r = evaluate(self.packet())
        self.assertEqual(r["decision"], "NO_HAZARD_DETECTED_NOT_APPROVED")
        self.assertFalse(r["authorization_to_build"])

    def test_missing_source_and_bad_number_fail_closed(self):
        with self.assertRaises(InvalidPacket):
            evaluate({"repository_full_name": "owner/repo", "issue_number": True, "issue_body": "bounty"})
        with self.assertRaises(InvalidPacket):
            evaluate({"repository_full_name": "owner/repo", "issue_number": 1})


if __name__ == "__main__":
    unittest.main()
