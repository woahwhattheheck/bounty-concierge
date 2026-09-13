import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from concierge.payout_escalation import (
    PayoutEscalationEvidenceError,
    PayoutEscalationInputError,
    closeout_snapshot_sha256,
    compile_payout_escalation,
    history_capture_sha256,
    history_row_sha256,
    policy_receipt,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


class PayoutEscalationTests(unittest.TestCase):
    AS_OF = utc("2026-09-13T12:00:00Z")
    MERGED = "2026-09-12T18:00:00Z"
    WALLET = "zov-wallet"

    def closeout(self, *, merged_at=None, state="MERGED", cash_status="not_inferred", amount="30", currency="RTC", followup=None):
        return {
            "schema_version": 1,
            "items": [{
                "repo": "Sponsor/project", "pr": 17, "canonical_url": "https://github.com/Sponsor/project/pull/17",
                "head_sha": "a" * 40, "advertised_amount": amount, "currency": currency,
                "state": state, "merged_at": merged_at or self.MERGED,
                "next_action": "route_settlement_followup", "reason": "merged_without_settlement_followup_evidence",
                "new_feedback_count": 0, "current_change_request_count": 0, "latest_feedback": None,
                "settlement_followup_url": followup, "cash_status": cash_status,
            }],
        }

    def row(self, *, status="pending", amount="30", timestamp="2026-09-13T02:00:00Z", tx_hash="tx-001", sender="treasury", recipient=None):
        row = {"type": "transfer_in", "amount": amount, "from": sender, "tx_hash": tx_hash}
        if status is not None: row["status"] = status
        if timestamp is not None: row["timestamp"] = timestamp
        if recipient is not None: row["to"] = recipient
        return row

    def history(self, rows):
        return {"ok": True, "miner_id": self.WALLET, "transactions": rows, "total": len(rows)}

    def bindings(
        self,
        rows,
        *,
        closeout=None,
        history=None,
        repo="Sponsor/project",
        pr=17,
        wallet=None,
        policy_version=None,
    ):
        closeout = closeout or self.closeout()
        history = history or self.history(rows)
        return {
            "schema_version": 1,
            "policy_version": policy_version or policy_receipt()["policy_version"],
            "wallet": wallet or self.WALLET,
            "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history),
            "items": [{
                "repo": repo,
                "pr": pr,
                "history_sha256s": [history_row_sha256(row, wallet=self.WALLET) for row in rows],
            }],
        }

    def compile(self, rows=(), **kwargs):
        rows = list(rows)
        closeout = kwargs.pop("closeout", self.closeout())
        history = kwargs.pop("history", self.history(rows))
        bindings = kwargs.pop("bindings", None)
        if bindings is None:
            bindings = self.bindings(rows, closeout=closeout, history=history)
        return compile_payout_escalation(
            closeout,
            history,
            bindings,
            as_of=kwargs.pop("as_of", self.AS_OF),
        )

    def expect_input(self, fn):
        with self.assertRaises(PayoutEscalationInputError): fn()

    def expect_evidence(self, fn):
        with self.assertRaises(PayoutEscalationEvidenceError): fn()

    def test_policy_is_versioned_and_self_bound(self):
        policy = policy_receipt()
        self.assertEqual(policy["currency"], "RTC")
        self.assertEqual(policy["transfer_initiation_hours"], 24)
        self.assertEqual(policy["pending_confirmation_hours"], 24)
        self.assertEqual(len(policy["policy_sha256"]), 64)
        self.assertEqual(len(policy["source_git_blob_sha1"]), 40)

    def test_no_transfer_within_24h_waits_without_cash_inference(self):
        closeout = self.closeout(merged_at="2026-09-13T00:00:01Z")
        out = self.compile(closeout=closeout)
        row = out["items"][0]
        self.assertEqual(row["action"], "await_transfer_initiation_window")
        self.assertEqual(row["cash_status"], "not_inferred")
        self.assertEqual(out["cash_authority"], "none")
        self.assertEqual(out["external_action_authority"], "none")

    def test_no_transfer_at_24h_is_owner_review_missing_transfer(self):
        closeout = self.closeout(merged_at="2026-09-12T12:00:00Z")
        out = self.compile(closeout=closeout)
        self.assertEqual(out["items"][0]["action"], "owner_review_missing_transfer")

    def test_pending_transfer_within_24h_monitors(self):
        row = self.row(timestamp="2026-09-13T02:00:00Z")
        out = self.compile([row])
        item = out["items"][0]
        self.assertEqual(item["action"], "monitor_pending_confirmation")
        self.assertEqual(item["pending_confirmation_expected_by"], "2026-09-14T02:00:00Z")
        self.assertEqual(item["bound_pending_count"], 1)

    def test_pending_transfer_at_24h_escalates_owner_review(self):
        row = self.row(timestamp="2026-09-12T12:00:00Z")
        closeout = self.closeout(merged_at="2026-09-12T10:00:00Z")
        out = self.compile([row], closeout=closeout)
        self.assertEqual(out["items"][0]["action"], "owner_review_pending_overdue")

    def test_confirmed_transfer_routes_existing_settlement_and_does_not_recognize_cash(self):
        row = self.row(status="confirmed", timestamp="2026-09-13T02:00:00Z")
        out = self.compile([row])
        item = out["items"][0]
        self.assertEqual(item["action"], "run_revenue_settlement")
        self.assertEqual(item["cash_status"], "not_inferred")
        self.assertEqual(item["bound_confirmed_count"], 1)

    def test_missing_status_is_confirmed_candidate_compatible_with_settlement(self):
        row = self.row(status=None, timestamp=None)
        out = self.compile([row])
        self.assertEqual(out["items"][0]["action"], "run_revenue_settlement")

    def test_failed_transfer_is_owner_review(self):
        row = self.row(status="failed")
        out = self.compile([row])
        self.assertEqual(out["items"][0]["action"], "owner_review_failed_transfer")

    def test_followup_url_does_not_suppress_overdue_classification(self):
        closeout = self.closeout(merged_at="2026-09-12T12:00:00Z", followup="https://github.com/Sponsor/project/issues/1")
        out = self.compile(closeout=closeout)
        self.assertEqual(out["items"][0]["action"], "owner_review_missing_transfer")
        self.assertEqual(out["items"][0]["settlement_followup_url"], "https://github.com/Sponsor/project/issues/1")

    def test_wrong_wallet_binding_fails(self):
        self.expect_evidence(lambda: self.compile(bindings=self.bindings([], wallet="other-wallet", closeout=self.closeout(), history=self.history([]))))

    def test_unmerged_closeout_fails(self):
        self.expect_input(lambda: self.compile(closeout=self.closeout(state="OPEN")))

    def test_existing_cash_claim_fails(self):
        self.expect_input(lambda: self.compile(closeout=self.closeout(cash_status="verified_paid")))

    def test_non_rtc_currency_fails_policy_boundary(self):
        self.expect_input(lambda: self.compile(closeout=self.closeout(currency="USD")))

    def test_future_merge_fails(self):
        self.expect_evidence(lambda: self.compile(closeout=self.closeout(merged_at="2026-09-14T00:00:00Z")))

    def test_future_transfer_timestamp_fails(self):
        row = self.row(timestamp="2026-09-14T00:00:00Z")
        self.expect_evidence(lambda: self.compile([row]))

    def test_premerge_transfer_fails_bounty_attribution(self):
        row = self.row(timestamp="2026-09-12T17:59:59Z")
        self.expect_evidence(lambda: self.compile([row]))

    def test_missing_pending_timestamp_fails(self):
        row = self.row(timestamp=None)
        self.expect_evidence(lambda: self.compile([row]))

    def test_nonincoming_or_self_funded_transfer_fails(self):
        row = self.row(); row["type"] = "reward"
        self.expect_evidence(lambda: self.compile([row]))
        row2 = self.row(sender=self.WALLET)
        self.expect_evidence(lambda: self.compile([row2]))

    def test_explicit_wrong_recipient_fails(self):
        row = self.row(recipient="other-wallet")
        self.expect_evidence(lambda: self.compile([row]))

    def test_unknown_status_fails(self):
        row = self.row(status="stuck")
        self.expect_evidence(lambda: self.compile([row]))

    def test_missing_or_malformed_transaction_identity_fails(self):
        row = self.row(tx_hash="")
        self.expect_evidence(lambda: self.compile([row]))
        row2 = self.row(tx_hash="bad tx id")
        self.expect_evidence(lambda: self.compile([row2]))

    def test_history_row_reuse_across_prs_fails(self):
        row = self.row()
        closeout = self.closeout()
        second = dict(closeout["items"][0]); second["pr"] = 18; second["canonical_url"] = "https://github.com/Sponsor/project/pull/18"
        closeout["items"].append(second)
        fp = history_row_sha256(row, wallet=self.WALLET)
        history = self.history([row])
        bindings = {
            "schema_version": 1, "policy_version": policy_receipt()["policy_version"], "wallet": self.WALLET,
            "closeout_sha256": closeout_snapshot_sha256(closeout),
            "history_capture_sha256": history_capture_sha256(history),
            "items": [
                {"repo": "Sponsor/project", "pr": 17, "history_sha256s": [fp]},
                {"repo": "Sponsor/project", "pr": 18, "history_sha256s": [fp]},
            ],
        }
        self.expect_evidence(lambda: self.compile([row], closeout=closeout, history=history, bindings=bindings))

    def test_transaction_identity_reuse_under_modified_rows_fails(self):
        row1 = self.row(tx_hash="same-tx")
        row2 = self.row(tx_hash="same-tx", timestamp="2026-09-13T03:00:00Z")
        history = self.history([row1, row2])
        bindings = self.bindings([row1, row2], history=history)
        self.expect_evidence(lambda: self.compile([row1, row2], history=history, bindings=bindings))

    def test_duplicate_indistinguishable_history_rows_fail(self):
        row = self.row()
        history = self.history([row, dict(row)])
        bindings = self.bindings([row], history=history)
        self.expect_evidence(lambda: self.compile([row, dict(row)], history=history, bindings=bindings))

    def test_absent_bound_hash_fails(self):
        row = self.row()
        history = self.history([])
        bindings = self.bindings([row], history=history)
        self.expect_evidence(lambda: self.compile([], history=history, bindings=bindings))

    def test_binding_unknown_pr_fails(self):
        self.expect_input(lambda: self.compile(bindings=self.bindings([], pr=999, closeout=self.closeout(), history=self.history([]))))

    def test_stale_policy_binding_fails(self):
        self.expect_input(lambda: self.compile(bindings=self.bindings([], policy_version="old/v0", closeout=self.closeout(), history=self.history([]))))

    def test_overpayment_evidence_fails(self):
        row = self.row(amount="31")
        self.expect_evidence(lambda: self.compile([row]))

    def test_split_pending_rows_use_earliest_confirmation_deadline(self):
        rows = [
            self.row(amount="10", timestamp="2026-09-13T01:00:00Z", tx_hash="tx-a"),
            self.row(amount="20", timestamp="2026-09-13T03:00:00Z", tx_hash="tx-b"),
        ]
        out = self.compile(rows)
        self.assertEqual(out["items"][0]["pending_confirmation_expected_by"], "2026-09-14T01:00:00Z")
        self.assertEqual(out["items"][0]["bound_amount_rtc"], "30")

    def test_mixed_confirmed_and_pending_stays_pending_timing_only(self):
        rows = [
            self.row(status="confirmed", amount="10", timestamp="2026-09-13T01:00:00Z", tx_hash="tx-a"),
            self.row(status="pending", amount="20", timestamp="2026-09-13T03:00:00Z", tx_hash="tx-b"),
        ]
        out = self.compile(rows)
        item = out["items"][0]
        self.assertEqual(item["action"], "monitor_pending_confirmation")
        self.assertEqual(item["cash_status"], "not_inferred")
        self.assertEqual(item["bound_confirmed_count"], 1)
        self.assertEqual(item["bound_pending_count"], 1)

    def test_normalized_capture_preserves_wallet_provenance(self):
        row = self.row()
        history = {"schema_version": 1, "source": "rustchain_wallet_history", "wallet": self.WALLET, "items": [row]}
        out = self.compile([row], history=history)
        self.assertEqual(out["history_source"], "captured_wallet")

    def test_unbound_legacy_history_capture_is_refused(self):
        self.expect_input(lambda: self.compile(history={"schema_version": 1, "items": []}))

    def test_bool_int_aliases_fail(self):
        closeout = self.closeout(); closeout["items"][0]["pr"] = True
        self.expect_input(lambda: self.compile(closeout=closeout))
        history = {"ok": True, "miner_id": self.WALLET, "transactions": [], "total": False}
        self.expect_input(lambda: self.compile(history=history))

    def test_receipt_is_deterministic_and_self_integrity_changes_with_evidence(self):
        one = self.compile([])
        two = self.compile([])
        self.assertEqual(one, two)
        row = self.row()
        three = self.compile([row])
        self.assertNotEqual(one["receipt_sha256"], three["receipt_sha256"])

    def test_closeout_with_higher_priority_repair_obligation_is_refused(self):
        closeout = self.closeout(); closeout["items"][0]["next_action"] = "repair_requested"
        self.expect_input(lambda: self.compile(closeout=closeout))

    def test_binding_closeout_snapshot_drift_fails(self):
        closeout = self.closeout(); history = self.history([])
        bindings = self.bindings([], closeout=closeout, history=history)
        closeout["items"][0]["advertised_amount"] = "31"
        self.expect_evidence(lambda: self.compile(closeout=closeout, history=history, bindings=bindings))

    def test_binding_history_snapshot_drift_fails(self):
        row = self.row(); closeout = self.closeout(); history = self.history([row])
        bindings = self.bindings([row], closeout=closeout, history=history)
        history["transactions"][0]["timestamp"] = "2026-09-13T03:00:00Z"
        self.expect_evidence(lambda: self.compile([history["transactions"][0]], closeout=closeout, history=history, bindings=bindings))

    def test_malformed_followup_url_is_refused(self):
        closeout = self.closeout(followup="not-a-url")
        self.expect_input(lambda: self.compile(closeout=closeout))

    def test_as_of_must_be_trusted_timezone_aware(self):
        closeout = self.closeout(); history = self.history([])
        with self.assertRaises(PayoutEscalationInputError):
            compile_payout_escalation(closeout, history, self.bindings([], closeout=closeout, history=history), as_of=datetime(2026, 9, 13, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
