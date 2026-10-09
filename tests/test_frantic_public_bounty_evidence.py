# SPDX-License-Identifier: MIT
"""Only the first-party claim-window observation contract, with no provider calls."""
import copy
from datetime import datetime, timezone
import unittest

from concierge.frantic_public_bounty_evidence import FranticObservationError, evaluate_public_bounty


class FranticPublicBountyEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "ok": True,
            "bounty": {
                "number": 120,
                "api_url": "https://gofrantic.com/v1/bounties/120",
                "page_url": "https://gofrantic.com/bounties/120",
                "price_cents": 100,
                "fee_cents": 1500,
                "funded": True,
                "claim_progress": {"capacity": 150, "occupied": 150, "available": 0},
                "posting_status": "posted", "work_status": "open", "cancellation_status": None,
            },
            "actions": {"claim": {"available": False, "state": "configured_closed", "requires": [], "reason": "no open claim slots"}},
        }
        self.time = datetime(2026, 10, 9, 5, 30, tzinfo=timezone.utc)

    def evaluate(self, value):
        return evaluate_public_bounty(120, value, raw_sha256="a" * 64, observed_at=self.time)

    def test_official_closed_and_underpaid_listing_blocked(self):
        result = self.evaluate(self.fixture)
        self.assertFalse(result["dispatch"])
        self.assertFalse(result["candidate_for_independent_qualification"])
        self.assertEqual(result["funded_usd"], "1.00")
        self.assertEqual(result["fee_usd"], "15.00")
        self.assertEqual(result["available_slots"], 0)
        self.assertEqual(result["claim_gate_state"], "configured_closed")
        self.assertIn("BELOW_15_USD_FLOOR", result["reason_codes"])
        self.assertIn("NO_FREE_CLAIM_SLOTS", result["reason_codes"])
        self.assertIn("CLAIM_GATE_CLOSED", result["reason_codes"])

    def test_viable_listing_remains_advisory_only(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["bounty"].update(price_cents=2000, fee_cents=200, claim_progress={"capacity": 10, "occupied": 3, "available": 7})
        fixture["actions"]["claim"].update(available=True, state="available")
        result = self.evaluate(fixture)
        self.assertTrue(result["candidate_for_independent_qualification"])
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["reason_codes"], ["FURTHER_SPONSOR_AND_CLAIMANT_CHECKS_REQUIRED"])

    def test_identity_required_is_not_available_to_dispatch(self):
        fixture = copy.deepcopy(self.fixture)
        fixture["bounty"].update(price_cents=2000, claim_progress={"capacity": 10, "occupied": 3, "available": 7})
        fixture["actions"]["claim"].update(available=False, state="requires_identity")
        self.assertIn("CLAIM_REQUIRES_AUTHENTICATED_IDENTITY", self.evaluate(fixture)["reason_codes"])

    def test_malformed_missing_fields_or_url_fail_closed(self):
        for change in ("missing_claim", "mismatch_url", "contradictory_action", "negative_slots"):
            fixture = copy.deepcopy(self.fixture)
            if change == "missing_claim":
                fixture["actions"].pop("claim")
            if change == "mismatch_url":
                fixture["bounty"]["api_url"] = "https://example.com/v1/bounties/120"
            if change == "contradictory_action":
                fixture["actions"]["claim"].update(available=True, state="configured_closed")
            if change == "negative_slots":
                fixture["bounty"]["claim_progress"]["available"] = -1
            with self.subTest(change=change), self.assertRaises(FranticObservationError):
                self.evaluate(fixture)


if __name__ == "__main__":
    unittest.main()
