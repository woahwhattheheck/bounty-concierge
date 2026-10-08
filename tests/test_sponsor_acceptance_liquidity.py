# SPDX-License-Identifier: MIT
"""Focused offline acceptance-liquidity cases; no provider requests."""
from datetime import datetime, timedelta, timezone
import copy
import unittest

from concierge.sponsor_acceptance_liquidity import (
    INPUT_SCHEMA, SponsorLiquidityInputError, compile_liquidity_gate, verify_receipt,
)


def data(open_count=0, owned_merges=0, complete=True):
    now = datetime(2026, 10, 8, 22, 42, tzinfo=timezone.utc)
    fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")
    return {
        "schema": INPUT_SCHEMA, "repository": "Example/Project", "actor_login": "woahwhattheheck",
        "observed_at": fmt(now), "evaluated_at": fmt(now),
        "window_start_at": fmt(now - timedelta(days=30)), "max_age_seconds": 900,
        "all_open_prs": complete, "all_closed_prs_since_window": complete,
        "open_prs": [
            {"number": n, "author_login": "woahwhattheheck",
             "created_at": fmt(now - timedelta(hours=2))}
            for n in range(1, open_count + 1)
        ],
        "closed_prs": [
            {"number": 100 + n, "author_login": "woahwhattheheck",
             "created_at": fmt(now - timedelta(days=3)),
             "closed_at": fmt(now - timedelta(days=2)),
             "merged_at": fmt(now - timedelta(days=2, minutes=1))}
            for n in range(1, owned_merges + 1)
        ],
    }


class SponsorAcceptanceLiquidityTests(unittest.TestCase):
    def test_unaccepted_backlog_holds_new_build_not_existing_claims(self):
        result = compile_liquidity_gate(data(open_count=30))
        self.assertEqual(result["disposition"], "HOLD_NEW_BUILD")
        self.assertIn("EIGHT_PLUS_OPEN_WITH_ZERO_ACCEPTED", result["reason_codes"])
        self.assertFalse(result["authority"]["existing_work_cancellation_authority"])
        self.assertTrue(verify_receipt(result))

    def test_verified_merges_and_low_backlog_leave_other_gates_in_charge(self):
        result = compile_liquidity_gate(data(open_count=7, owned_merges=2))
        self.assertEqual(result["disposition"], "REVIEW_OTHER_GATES")
        self.assertTrue(verify_receipt(result))

    def test_incomplete_snapshot_cannot_clear_and_tampering_is_rejected(self):
        result = compile_liquidity_gate(data(complete=False))
        self.assertEqual(result["disposition"], "HOLD_EVIDENCE")
        bad = copy.deepcopy(result)
        bad["counts"]["actor_merged_in_window"] = 100
        self.assertFalse(verify_receipt(bad))

    def test_conflicting_or_future_pr_timestamps_fail_closed(self):
        request = data(open_count=1, owned_merges=1)
        request["closed_prs"][0]["number"] = 1
        with self.assertRaises(SponsorLiquidityInputError):
            compile_liquidity_gate(request)
        request = data(open_count=1)
        request["open_prs"][0]["created_at"] = "2026-10-09T00:00:00Z"
        with self.assertRaises(SponsorLiquidityInputError):
            compile_liquidity_gate(request)


if __name__ == "__main__":
    unittest.main()
