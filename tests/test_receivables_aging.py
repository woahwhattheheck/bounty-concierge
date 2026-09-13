# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from concierge import receivables_aging as ra


NOW = datetime(2026, 9, 13, 13, 0, 0, tzinfo=timezone.utc)


def policy(followup=24, escalate=72):
    return {
        "schema": "bounty-receivables-aging-policy/v1",
        "version": 1,
        "followup_after_hours": followup,
        "escalate_after_hours": escalate,
    }


def closeout_row(pr, *, hours=10, amount="10", state="MERGED", followup=None):
    merged_at = None
    if state == "MERGED":
        merged_at = datetime.fromtimestamp(NOW.timestamp() - hours * 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "repo": "org/repo",
        "pr": pr,
        "canonical_url": f"https://github.com/org/repo/pull/{pr}",
        "head_sha": f"{pr:040x}"[-40:],
        "advertised_amount": amount,
        "currency": "RTC",
        "state": state,
        "merged_at": merged_at,
        "next_action": "monitor_settlement",
        "reason": "fixture",
        "new_feedback_count": 0,
        "current_change_request_count": 0,
        "latest_feedback": None,
        "settlement_followup_url": followup,
        "cash_status": "not_inferred",
    }


def settlement_row(source, *, status="not_inferred", verified="0", evidence=None):
    evidence = evidence or []
    return {
        "repo": source["repo"],
        "pr": source["pr"],
        "state": source["state"],
        "currency": source["currency"],
        "advertised_amount": source["advertised_amount"],
        "verified_amount": verified,
        "cash_status": status,
        "payment_evidence": [
            {"history_sha256": value} for value in evidence
        ],
    }


class ReceivablesAgingTests(unittest.TestCase):
    def compile(self, closeout, settlement, pol=None):
        with mock.patch.object(ra, "_trusted_utc_now", return_value=NOW), \
             mock.patch.object(ra.revenue_closeout, "build_closeout_queue", return_value=copy.deepcopy(closeout)) as closeout_call, \
             mock.patch.object(ra.revenue_settlement, "_query_canonical_history", return_value=([{"history": "canonical"}], "wallet-1")) as history_call, \
             mock.patch.object(ra.revenue_settlement, "reconcile_cash", return_value=copy.deepcopy(settlement)) as settle_call:
            result = ra.compile_receivables_review(
                [{"manifest": "input"}],
                [{"binding": "input"}],
                pol or policy(),
                wallet="wallet-1",
            )
        return result, closeout_call, history_call, settle_call

    def test_happy_path_orders_escalate_followup_monitor_settled(self):
        rows = [
            closeout_row(1, hours=100, followup="https://example.test/f/1"),
            closeout_row(2, hours=30),
            closeout_row(3, hours=10),
            closeout_row(4, hours=40, followup="https://example.test/f/4"),
            closeout_row(5, hours=100, amount="8"),
        ]
        settled = [
            settlement_row(rows[0]),
            settlement_row(rows[1]),
            settlement_row(rows[2]),
            settlement_row(rows[3], status="partially_verified", verified="4", evidence=["a" * 64]),
            settlement_row(rows[4], status="verified_paid", verified="8", evidence=["b" * 64]),
        ]
        receipt, _, _, _ = self.compile(rows, settled)
        self.assertEqual(
            [row["action"] for row in receipt["receivables"]],
            [
                "OWNER_ESCALATION_REVIEW",
                "OWNER_FOLLOWUP_REVIEW",
                "MONITOR_ROUTED_FOLLOWUP",
                "MONITOR_UNSETTLED",
                "SETTLED_VERIFIED",
            ],
        )
        self.assertEqual(receipt["summary"]["verified_rtc_total"], "12")
        self.assertEqual(receipt["summary"]["outstanding_rtc_total"], "36")
        self.assertEqual(receipt["summary"]["owner_escalation_review_items"], 1)
        self.assertEqual(receipt["summary"]["owner_followup_review_items"], 1)

    def test_calls_live_closeout_and_canonical_wallet_with_queried_source(self):
        row = closeout_row(1)
        receipt, closeout_call, history_call, settle_call = self.compile([row], [settlement_row(row)])
        closeout_call.assert_called_once_with([{"manifest": "input"}], max_pages=10)
        history_call.assert_called_once_with("wallet-1")
        kwargs = settle_call.call_args.kwargs
        self.assertEqual(kwargs["history_wallet"], "wallet-1")
        self.assertEqual(kwargs["history_source"], "queried_wallet")
        self.assertEqual(receipt["history_source"], "queried_wallet")

    def test_threshold_boundaries_are_inclusive(self):
        follow = closeout_row(1, hours=24)
        escalate = closeout_row(2, hours=72, followup="https://example.test/f/2")
        receipt, *_ = self.compile([follow, escalate], [settlement_row(follow), settlement_row(escalate)])
        actions = {row["pr"]: row["action"] for row in receipt["receivables"]}
        self.assertEqual(actions[1], "OWNER_FOLLOWUP_REVIEW")
        self.assertEqual(actions[2], "OWNER_ESCALATION_REVIEW")

    def test_partial_payment_preserves_exact_outstanding(self):
        row = closeout_row(1, amount="10.5", hours=30)
        paid = settlement_row(row, status="partially_verified", verified="3.25", evidence=["c" * 64])
        receipt, *_ = self.compile([row], [paid])
        self.assertEqual(receipt["receivables"][0]["outstanding_rtc"], "7.25")
        self.assertEqual(receipt["summary"]["verified_rtc_total"], "3.25")

    def test_nonmerged_items_bind_scope_but_do_not_become_receivables(self):
        merged = closeout_row(1, hours=30)
        open_row = closeout_row(2, state="OPEN")
        receipt, *_ = self.compile([merged, open_row], [settlement_row(merged), settlement_row(open_row)])
        self.assertEqual(receipt["summary"]["scanned_item_count"], 2)
        self.assertEqual(receipt["summary"]["merged_item_count"], 1)
        self.assertEqual([row["pr"] for row in receipt["receivables"]], [1])

    def test_scope_missing_settlement_fails_closed(self):
        first = closeout_row(1)
        second = closeout_row(2)
        with self.assertRaisesRegex(ra.ReceivablesInputError, "scope"):
            self.compile([first, second], [settlement_row(first)])

    def test_duplicate_settlement_identity_fails(self):
        first = closeout_row(1)
        second = closeout_row(2)
        dup = settlement_row(first)
        with self.assertRaisesRegex(ra.ReceivablesInputError, "duplicate settlement"):
            self.compile([first, second], [dup, copy.deepcopy(dup)])

    def test_state_mismatch_fails(self):
        row = closeout_row(1)
        settled = settlement_row(row)
        settled["state"] = "OPEN"
        with self.assertRaisesRegex(ra.ReceivablesInputError, "state disagrees"):
            self.compile([row], [settled])

    def test_advertised_amount_mismatch_fails(self):
        row = closeout_row(1)
        settled = settlement_row(row)
        settled["advertised_amount"] = "999"
        with self.assertRaisesRegex(ra.ReceivablesInputError, "advertised amount disagrees"):
            self.compile([row], [settled])

    def test_verified_paid_requires_full_amount_and_evidence(self):
        row = closeout_row(1, amount="10")
        bad = settlement_row(row, status="verified_paid", verified="9", evidence=["d" * 64])
        with self.assertRaisesRegex(ra.ReceivablesInputError, "verified_paid"):
            self.compile([row], [bad])

    def test_partial_requires_strictly_partial_amount_and_evidence(self):
        row = closeout_row(1, amount="10")
        bad = settlement_row(row, status="partially_verified", verified="10", evidence=["e" * 64])
        with self.assertRaisesRegex(ra.ReceivablesInputError, "partially_verified"):
            self.compile([row], [bad])

    def test_not_inferred_cannot_carry_cash_or_evidence(self):
        row = closeout_row(1)
        bad = settlement_row(row, status="not_inferred", verified="1", evidence=["f" * 64])
        with self.assertRaisesRegex(ra.ReceivablesInputError, "not_inferred"):
            self.compile([row], [bad])

    def test_payment_evidence_cannot_cross_support_items(self):
        first = closeout_row(1)
        second = closeout_row(2)
        digest = "1" * 64
        one = settlement_row(first, status="partially_verified", verified="1", evidence=[digest])
        two = settlement_row(second, status="partially_verified", verified="1", evidence=[digest])
        with self.assertRaisesRegex(ra.ReceivablesInputError, "cross-used"):
            self.compile([first, second], [one, two])

    def test_future_merge_time_fails(self):
        row = closeout_row(1)
        row["merged_at"] = "2026-09-13T13:00:01Z"
        with self.assertRaisesRegex(ra.ReceivablesInputError, "future"):
            self.compile([row], [settlement_row(row)])

    def test_wrong_canonical_url_fails(self):
        row = closeout_row(1)
        row["canonical_url"] = "https://example.test/not-github"
        with self.assertRaisesRegex(ra.ReceivablesInputError, "canonical URL"):
            self.compile([row], [settlement_row(row)])

    def test_invalid_policy_order_fails_before_network(self):
        with self.assertRaisesRegex(ra.ReceivablesInputError, "must exceed"):
            ra.compile_receivables_review([{}], [], policy(72, 24), wallet="wallet-1")

    def test_bool_policy_threshold_rejected(self):
        bad = policy()
        bad["followup_after_hours"] = True
        with self.assertRaises(ra.ReceivablesInputError):
            ra.compile_receivables_review([{}], [], bad, wallet="wallet-1")

    def test_unknown_policy_key_rejected(self):
        bad = policy()
        bad["surprise"] = 1
        with self.assertRaisesRegex(ra.ReceivablesInputError, "extra=surprise"):
            ra.compile_receivables_review([{}], [], bad, wallet="wallet-1")

    def test_receipt_integrity_detects_tamper(self):
        row = closeout_row(1)
        receipt, *_ = self.compile([row], [settlement_row(row)])
        self.assertTrue(ra.verify_receipt_integrity(receipt))
        receipt["summary"]["outstanding_rtc_total"] = "999"
        self.assertFalse(ra.verify_receipt_integrity(receipt))

    def test_authority_ceiling_is_explicit(self):
        row = closeout_row(1)
        receipt, *_ = self.compile([row], [settlement_row(row)])
        summary = receipt["summary"]
        self.assertFalse(summary["merge_used_as_cash"])
        self.assertFalse(summary["accounting_revenue_claim"])
        self.assertFalse(summary["autonomous_contact_authorized"])
        self.assertFalse(summary["wallet_or_provider_mutation_authorized"])
        self.assertFalse(summary["spend_authorized"])

    def test_same_authority_snapshot_is_deterministic(self):
        row = closeout_row(1, hours=30)
        first, *_ = self.compile([row], [settlement_row(row)])
        second, *_ = self.compile([row], [settlement_row(row)])
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_no_public_as_of_backdate_parameter(self):
        self.assertNotIn("as_of", ra.compile_receivables_review.__annotations__)
        with self.assertRaises(TypeError):
            ra.compile_receivables_review([{}], [], policy(), wallet="wallet-1", as_of=NOW)

    def test_manifest_root_is_strict(self):
        with self.assertRaisesRegex(ra.ReceivablesInputError, "extra=other"):
            ra._manifest_items({"schema_version": 1, "items": [], "other": 1})

    def test_output_refuses_existing_file_and_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            target.write_text("old")
            with self.assertRaisesRegex(ra.ReceivablesInputError, "refusing to overwrite"):
                ra._write_output(str(target), {"ok": True})
            target.unlink()
            real = Path(tmp) / "real.json"
            real.write_text("old")
            target.symlink_to(real)
            with self.assertRaisesRegex(ra.ReceivablesInputError, "refusing to overwrite"):
                ra._write_output(str(target), {"ok": True})
            self.assertEqual(real.read_text(), "old")


if __name__ == "__main__":
    unittest.main()
