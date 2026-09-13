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

    def test_missing_binding_for_closeout_item_fails_closed(self):
        closeout = {
            "schema_version": 1,
            "items": [{
                "repo": "Sponsor/project", "pr": 17,
                "canonical_url": "https://github.com/Sponsor/project/pull/17",
                "head_sha": "a" * 40, "advertised_amount": "30", "currency": "RTC",
                "state": "MERGED", "merged_at": "2026-09-12T12:00:00Z",
                "next_action": "route_settlement_followup",
                "reason": "merged_without_settlement_followup_evidence",
                "new_feedback_count": 0, "current_change_request_count": 0,
                "latest_feedback": None, "settlement_followup_url": None,
                "cash_status": "not_inferred",
            }],
        }
        history = {"ok": True, "miner_id": "wallet", "transactions": [], "total": 0}
        bindings = {
            "schema_version": 1, "policy_version": policy_receipt()["policy_version"],
            "wallet": "wallet", "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history), "items": [],
        }
        from concierge.payout_escalation import PayoutEscalationInputError
        with self.assertRaises(PayoutEscalationInputError):
            compile_payout_escalation(
                closeout, history, bindings,
                as_of=datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc),
            )

    def test_current_provider_integer_epoch_timestamp_is_supported(self):
        from concierge.payout_escalation import history_row_sha256
        closeout = {
            "schema_version": 1,
            "items": [{
                "repo": "Sponsor/project", "pr": 17,
                "canonical_url": "https://github.com/Sponsor/project/pull/17",
                "head_sha": "a" * 40, "advertised_amount": "30", "currency": "RTC",
                "state": "MERGED", "merged_at": "2026-09-12T18:00:00Z",
                "next_action": "route_settlement_followup",
                "reason": "merged_without_settlement_followup_evidence",
                "new_feedback_count": 0, "current_change_request_count": 0,
                "latest_feedback": None, "settlement_followup_url": None,
                "cash_status": "not_inferred",
            }],
        }
        row = {
            "type": "transfer_in", "amount": 30, "from": "treasury",
            "timestamp": 1789264800, "tx_hash": "tx-current-shape", "status": "pending",
        }
        history = {"ok": True, "miner_id": "wallet", "transactions": [row], "total": 1}
        bindings = {
            "schema_version": 1, "policy_version": policy_receipt()["policy_version"],
            "wallet": "wallet", "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history),
            "items": [{
                "repo": "Sponsor/project", "pr": 17,
                "history_sha256s": [history_row_sha256(row, wallet="wallet")],
            }],
        }
        out = compile_payout_escalation(
            closeout, history, bindings,
            as_of=datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(out["items"][0]["action"], "monitor_pending_confirmation")
        self.assertEqual(out["items"][0]["pending_confirmation_expected_by"], "2026-09-14T02:00:00Z")

    def test_history_timestamp_bool_and_out_of_range_epoch_fail_closed(self):
        from concierge.payout_escalation import (
            PayoutEscalationInputError, history_row_sha256,
        )
        for value in (True, -1, 253402300800):
            closeout = {
                "schema_version": 1,
                "items": [{
                    "repo": "Sponsor/project", "pr": 17,
                    "canonical_url": "https://github.com/Sponsor/project/pull/17",
                    "head_sha": "a" * 40, "advertised_amount": "30", "currency": "RTC",
                    "state": "MERGED", "merged_at": "2026-09-12T18:00:00Z",
                    "next_action": "route_settlement_followup",
                    "reason": "merged_without_settlement_followup_evidence",
                    "new_feedback_count": 0, "current_change_request_count": 0,
                    "latest_feedback": None, "settlement_followup_url": None,
                    "cash_status": "not_inferred",
                }],
            }
            row = {
                "type": "transfer_in", "amount": 30, "from": "treasury",
                "timestamp": value, "tx_hash": "tx-current-shape", "status": "pending",
            }
            history = {"ok": True, "miner_id": "wallet", "transactions": [row], "total": 1}
            bindings = {
                "schema_version": 1, "policy_version": policy_receipt()["policy_version"],
                "wallet": "wallet", "closeout_sha256": closeout_snapshot_sha256(closeout),
                "history_capture_sha256": history_capture_sha256(history),
                "items": [{
                    "repo": "Sponsor/project", "pr": 17,
                    "history_sha256s": [history_row_sha256(row, wallet="wallet")],
                }],
            }
            with self.assertRaises(PayoutEscalationInputError):
                compile_payout_escalation(
                    closeout, history, bindings,
                    as_of=datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc),
                )

    def test_submicrosecond_timestamp_text_is_rejected_instead_of_truncated(self):
        closeout = {
            "schema_version": 1,
            "items": [{
                "repo": "Sponsor/project", "pr": 17,
                "canonical_url": "https://github.com/Sponsor/project/pull/17",
                "head_sha": "a" * 40, "advertised_amount": "30", "currency": "RTC",
                "state": "MERGED", "merged_at": "2026-09-12T12:00:00.1234567Z",
                "next_action": "route_settlement_followup",
                "reason": "merged_without_settlement_followup_evidence",
                "new_feedback_count": 0, "current_change_request_count": 0,
                "latest_feedback": None, "settlement_followup_url": None,
                "cash_status": "not_inferred",
            }],
        }
        history = {"ok": True, "miner_id": "wallet", "transactions": [], "total": 0}
        bindings = {
            "schema_version": 1, "policy_version": policy_receipt()["policy_version"],
            "wallet": "wallet", "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history),
            "items": [{"repo": "Sponsor/project", "pr": 17, "history_sha256s": []}],
        }
        from concierge.payout_escalation import PayoutEscalationInputError
        with self.assertRaises(PayoutEscalationInputError):
            compile_payout_escalation(
                closeout, history, bindings,
                as_of=datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc),
            )

    def test_cli_loader_rejects_fifo_without_blocking(self):
        import os
        import tempfile
        from concierge.payout_escalation import PayoutEscalationInputError, _load_json
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unsupported on this platform")
        with tempfile.TemporaryDirectory() as directory:
            fifo = os.path.join(directory, "input.json")
            os.mkfifo(fifo)
            with self.assertRaises(PayoutEscalationInputError):
                _load_json(fifo)

    def test_cli_loader_rejects_symlink_when_nofollow_is_available(self):
        import os
        import tempfile
        from concierge.payout_escalation import _load_json
        if not getattr(os, "O_NOFOLLOW", 0):
            self.skipTest("O_NOFOLLOW unsupported on this platform")
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "target.json")
            link = os.path.join(directory, "link.json")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write('{"ok":true}')
            os.symlink(target, link)
            with self.assertRaises(OSError):
                _load_json(link)

    def test_cli_loader_rejects_oversized_file_before_json_parse(self):
        import os
        import tempfile
        from concierge.payout_escalation import PayoutEscalationInputError, _MAX_JSON_BYTES, _load_json
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "large.json")
            with open(path, "wb") as handle:
                handle.truncate(_MAX_JSON_BYTES + 1)
            with self.assertRaises(PayoutEscalationInputError):
                _load_json(path)


if __name__ == "__main__":
    unittest.main()
