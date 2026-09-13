from datetime import datetime, timezone
import unittest

from concierge.payout_escalation import (
    closeout_snapshot_sha256, compile_payout_escalation,
    history_capture_sha256, policy_receipt,
)


class FractionalDeadlineTests(unittest.TestCase):
    def test_fractional_merge_deadline_is_rendered_losslessly_and_boundary_matches(self):
        closeout = {
            "schema_version": 1,
            "items": [{
                "repo": "Sponsor/project", "pr": 17,
                "canonical_url": "https://github.com/Sponsor/project/pull/17",
                "head_sha": "a" * 40, "advertised_amount": "30", "currency": "RTC",
                "state": "MERGED", "merged_at": "2026-09-12T12:00:00.500000Z",
                "next_action": "route_settlement_followup",
                "reason": "merged_without_settlement_followup_evidence",
                "new_feedback_count": 0, "current_change_request_count": 0,
                "latest_feedback": None, "settlement_followup_url": None,
                "cash_status": "not_inferred",
            }],
        }
        history = {"ok": True, "miner_id": "wallet", "transactions": [], "total": 0}
        bindings = {
            "schema_version": 1,
            "policy_version": policy_receipt()["policy_version"],
            "wallet": "wallet",
            "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history),
            "items": [{"repo": "Sponsor/project", "pr": 17, "history_sha256s": []}],
        }
        before = compile_payout_escalation(
            closeout, history, bindings,
            as_of=datetime(2026, 9, 13, 12, 0, 0, 499999, tzinfo=timezone.utc),
        )
        self.assertEqual(before["items"][0]["action"], "await_transfer_initiation_window")
        self.assertEqual(
            before["items"][0]["transfer_initiation_expected_by"],
            "2026-09-13T12:00:00.500000Z",
        )
        at = compile_payout_escalation(
            closeout, history, bindings,
            as_of=datetime(2026, 9, 13, 12, 0, 0, 500000, tzinfo=timezone.utc),
        )
        self.assertEqual(at["items"][0]["action"], "owner_review_missing_transfer")


if __name__ == "__main__":
    unittest.main()
