"""Focused regression for existing original-author PR collision admission."""
import unittest

from concierge.bounty_canonical_viability import (
    compile_bounty_canonical_viability,
    verify_receipt,
)
from concierge.bounty_value_router import compile_bounty_value_routing


NOW = "2026-10-08T20:00:00Z"
SEEN = "2026-10-08T19:59:00Z"
ISSUE_URL = "https://github.com/example/repo/issues/7"
PR_URL = "https://github.com/example/repo/pull/21"


def request(overlap: str) -> dict:
    value_receipt = compile_bounty_value_routing(
        {
            "schema": "bounty-value-routing/v1",
            "policy": {
                "schema": "bounty-value-routing-policy/v1",
                "max_evidence_age_seconds": 3600,
                "routes": {"main_queue": "main", "pile_10_49": "pile"},
                "assets": {"USD": {"active_floor": "50", "pile_floor": "10"}},
            },
            "evaluated_at": NOW,
            "candidates": [
                {
                    "work_id": "issue7",
                    "canonical_source_url": ISSUE_URL,
                    "reward_evidence": [
                        {
                            "scope": "ISSUE_SPECIFIC",
                            "authority": "FIRST_PARTY",
                            "amount": "60",
                            "asset": "USD",
                            "evidence_url": ISSUE_URL,
                            "observed_at": SEEN,
                        }
                    ],
                }
            ],
        }
    )
    return {
        "schema": "bounty-canonical-viability/v2",
        "value_gate": {"work_id": "issue7", "receipt": value_receipt},
        "actor_login": "woahwhattheheck",
        "canonical_issue_url": ISSUE_URL,
        "listing": {"url": "https://algora.io/example/bounties", "state": "OPEN", "observed_at": SEEN},
        "repository": {"full_name": "example/repo", "archived": False, "observed_at": SEEN},
        "issue": {
            "state": "OPEN",
            "state_reason": None,
            "acceptance": "ACCEPTED",
            "assignees": [],
            "observed_at": SEEN,
        },
        "reward": {
            "payment_path": "VERIFIED",
            "assignment_required": False,
            "actor_applied": True,
            "observed_at": SEEN,
        },
        "collisions": {
            "open_prs": [
                {
                    "url": PR_URL,
                    "author_login": "woahwhattheheck",
                    "overlap": overlap,
                    "observed_at": SEEN,
                }
            ],
            "active_claim_count": 0,
            "maintainer_confirmed_residual": False,
            "observed_at": SEEN,
        },
        "evaluated_at": NOW,
        "max_snapshot_age_seconds": 3600,
        "claim_pressure_threshold": 5,
    }


class ExistingContributorPRAdmissionTest(unittest.TestCase):
    def test_full_overlap_reuses_existing_original_author_pr(self):
        receipt = compile_bounty_canonical_viability(request("FULL"))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertEqual(receipt["advisory_next_action"], "RECOVER_EXISTING_ORIGINAL_AUTHOR_PR")
        self.assertIn("ACTOR_OWNED_FULL_PR_PRESENT", receipt["reason_codes"])
        self.assertTrue(verify_receipt(receipt))

    def test_unknown_overlap_requires_review(self):
        receipt = compile_bounty_canonical_viability(request("UNKNOWN"))
        self.assertEqual(receipt["disposition"], "HOLD")
        self.assertIn("ACTOR_OWNED_PR_OVERLAP_UNKNOWN", receipt["reason_codes"])
        self.assertTrue(verify_receipt(receipt))

    def test_partial_overlap_keeps_original_claim_route_open(self):
        receipt = compile_bounty_canonical_viability(request("PARTIAL"))
        self.assertEqual(receipt["disposition"], "READY_FOR_CLAIM_REVIEW")
        self.assertEqual(receipt["reason_codes"], [])
        self.assertTrue(verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
