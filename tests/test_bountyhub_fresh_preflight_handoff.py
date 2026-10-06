"""Focused handoff regression for fresh BountyHub targets."""
import unittest

from concierge.bounty_capture_batch import _shortlist
from concierge.bountyhub_fresh_targets import _exclude_assigned_exclusive


class FreshTargetPreflightHandoffTests(unittest.TestCase):
    def test_emits_lead_only_capture_batch_envelope(self):
        report = {
            "listings": [{
                "listing_id": "listing-1",
                "repo": "Owner/Repo",
                "number": 7,
                "assignment_type": "nonexclusive",
                "has_assignee": False,
            }]
        }
        selected = {
            "targets": [{"repo": "Owner/Repo", "number": 7}],
            "listing_ids_by_issue": {"owner/repo#7": ["listing-1"]},
        }

        result = _exclude_assigned_exclusive(report, selected)

        self.assertEqual(result["dispatch_status"], "LEAD")
        self.assertFalse(result["green_authorized"])
        self.assertTrue(result["requires_canonical_preflight"])
        self.assertTrue(result["requires_work_order_lease"])
        self.assertIn("swarm_reservation", result["targets"][0])
        self.assertEqual(
            result["canonical_preflight_candidates"],
            {"candidates": [{"repo": "Owner/Repo", "number": 7}]},
        )

        candidates, duplicate_count = _shortlist(
            result["canonical_preflight_candidates"]
        )
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(candidates, [{"repo": "owner/repo", "number": 7}])


if __name__ == "__main__":
    unittest.main()
