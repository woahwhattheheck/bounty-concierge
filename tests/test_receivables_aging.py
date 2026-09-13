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


def policy(review=24, aged=72):
    return {
        "schema": "bounty-merged-work-settlement-review-policy/v1",
        "version": 1,
        "review_after_hours": review,
        "aged_review_after_hours": aged,
    }


def closeout_row(pr, *, hours=10, amount="10", state="MERGED", route=None):
    merged_at = None
    if state == "MERGED":
        merged_at = datetime.fromtimestamp(
            NOW.timestamp() - hours * 3600, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
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
        "settlement_followup_url": route,
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
        "payment_evidence": [{"history_sha256": value} for value in evidence],
    }


class MergedSettlementReviewTests(unittest.TestCase):
    def compile(self, closeout, settlement, pol=None):
        with (
            mock.patch.object(ra, "_trusted_utc_now", return_value=NOW),
            mock.patch.object(
                ra.revenue_closeout,
                "build_closeout_queue",
                return_value=copy.deepcopy(closeout),
            ) as closeout_call,
            mock.patch.object(
                ra.revenue_settlement,
                "_query_canonical_history",
                return_value=([{"history": "canonical"}], "wallet-1"),
            ) as history_call,
            mock.patch.object(
                ra.revenue_settlement,
                "reconcile_cash",
                return_value=copy.deepcopy(settlement),
            ) as settle_call,
        ):
            result = ra.compile_receivables_review(
                [{"manifest": "input"}],
                [{"binding": "input"}],
                pol or policy(),
                wallet="wallet-1",
            )
        return result, closeout_call, history_call, settle_call

    def test_schema_migrates_away_from_receivable_authority(self):
        row = closeout_row(1, hours=100, amount="10")
        receipt, *_ = self.compile([row], [settlement_row(row)])
        self.assertEqual(
            receipt["schema"], "bounty-merged-work-settlement-review/v1"
        )
        self.assertNotIn("receivables", receipt)
        item = receipt["merged_work_review"][0]
        self.assertEqual(item["unverified_advertised_rtc"], "10")
        self.assertFalse(item["payable_obligation_proven"])
        self.assertFalse(item["unverified_advertised_amount_is_debt"])
        self.assertFalse(receipt["summary"]["unverified_advertised_amount_is_receivable"])
        self.assertFalse(receipt["summary"]["payable_obligation_claim"])
        self.assertFalse(receipt["summary"]["debt_claim"])
        self.assertNotIn("outstanding_rtc", json.dumps(receipt, sort_keys=True))

    def test_route_metadata_never_proves_prior_contact_or_changes_action(self):
        with_route = closeout_row(
            1, hours=30, route="https://example.test/settlement/1"
        )
        without_route = closeout_row(2, hours=30)
        receipt, *_ = self.compile(
            [with_route, without_route],
            [settlement_row(with_route), settlement_row(without_route)],
        )
        rows = {row["pr"]: row for row in receipt["merged_work_review"]}
        self.assertEqual(rows[1]["action"], "OWNER_UNSETTLED_REVIEW")
        self.assertEqual(rows[2]["action"], "OWNER_UNSETTLED_REVIEW")
        self.assertTrue(rows[1]["settlement_route_available"])
        self.assertFalse(rows[2]["settlement_route_available"])
        self.assertFalse(rows[1]["route_proves_prior_contact"])
        self.assertFalse(receipt["summary"]["prior_contact_claim"])

    def test_action_order_is_age_only_until_wallet_settles(self):
        aged = closeout_row(
            1, hours=100, route="https://example.test/settlement/1"
        )
        review = closeout_row(2, hours=30)
        monitor = closeout_row(3, hours=10)
        settled = closeout_row(4, hours=100, amount="8")
        receipt, *_ = self.compile(
            [aged, review, monitor, settled],
            [
                settlement_row(aged),
                settlement_row(review),
                settlement_row(monitor),
                settlement_row(
                    settled,
                    status="verified_paid",
                    verified="8",
                    evidence=["a" * 64],
                ),
            ],
        )
        self.assertEqual(
            [row["action"] for row in receipt["merged_work_review"]],
            [
                "OWNER_AGED_UNSETTLED_REVIEW",
                "OWNER_UNSETTLED_REVIEW",
                "MONITOR_UNSETTLED",
                "SETTLED_VERIFIED",
            ],
        )

    def test_threshold_boundaries_are_inclusive_without_contact_inference(self):
        review = closeout_row(1, hours=24)
        aged = closeout_row(
            2, hours=72, route="https://example.test/settlement/2"
        )
        receipt, *_ = self.compile(
            [review, aged], [settlement_row(review), settlement_row(aged)]
        )
        actions = {
            row["pr"]: row["action"] for row in receipt["merged_work_review"]
        }
        self.assertEqual(actions[1], "OWNER_UNSETTLED_REVIEW")
        self.assertEqual(actions[2], "OWNER_AGED_UNSETTLED_REVIEW")

    def test_partial_cash_preserves_unverified_advertised_arithmetic_only(self):
        row = closeout_row(1, amount="10.5", hours=30)
        partial = settlement_row(
            row,
            status="partially_verified",
            verified="3.25",
            evidence=["b" * 64],
        )
        receipt, *_ = self.compile([row], [partial])
        item = receipt["merged_work_review"][0]
        self.assertEqual(item["verified_rtc"], "3.25")
        self.assertEqual(item["unverified_advertised_rtc"], "7.25")
        self.assertFalse(item["payable_obligation_proven"])
        self.assertEqual(
            receipt["summary"]["unverified_advertised_rtc_total"], "7.25"
        )

    def test_full_wallet_settlement_is_verified_without_debt_claim(self):
        row = closeout_row(1, amount="10", hours=100)
        paid = settlement_row(
            row,
            status="verified_paid",
            verified="10",
            evidence=["c" * 64],
        )
        receipt, *_ = self.compile([row], [paid])
        item = receipt["merged_work_review"][0]
        self.assertEqual(item["action"], "SETTLED_VERIFIED")
        self.assertEqual(item["unverified_advertised_rtc"], "0")
        self.assertFalse(receipt["summary"]["accounting_revenue_claim"])

    def test_nonmerged_items_bind_scope_but_do_not_enter_merged_review(self):
        merged = closeout_row(1, hours=30)
        open_row = closeout_row(2, state="OPEN")
        receipt, *_ = self.compile(
            [merged, open_row],
            [settlement_row(merged), settlement_row(open_row)],
        )
        self.assertEqual(receipt["summary"]["scanned_item_count"], 2)
        self.assertEqual(receipt["summary"]["merged_item_count"], 1)
        self.assertEqual(
            [row["pr"] for row in receipt["merged_work_review"]], [1]
        )

    def test_calls_live_closeout_and_canonical_wallet(self):
        row = closeout_row(1)
        receipt, closeout_call, history_call, settle_call = self.compile(
            [row], [settlement_row(row)]
        )
        closeout_call.assert_called_once_with(
            [{"manifest": "input"}], max_pages=10
        )
        history_call.assert_called_once_with("wallet-1")
        kwargs = settle_call.call_args.kwargs
        self.assertEqual(kwargs["history_wallet"], "wallet-1")
        self.assertEqual(kwargs["history_source"], "queried_wallet")
        self.assertEqual(receipt["history_source"], "queried_wallet")

    def test_scope_missing_settlement_fails_closed(self):
        first = closeout_row(1)
        second = closeout_row(2)
        with self.assertRaisesRegex(ra.ReceivablesInputError, "scope"):
            self.compile([first, second], [settlement_row(first)])

    def test_duplicate_settlement_identity_fails_closed(self):
        first = closeout_row(1)
        second = closeout_row(2)
        duplicate = settlement_row(first)
        with self.assertRaisesRegex(
            ra.ReceivablesInputError, "duplicate settlement"
        ):
            self.compile(
                [first, second], [duplicate, copy.deepcopy(duplicate)]
            )

    def test_verified_paid_requires_full_amount_and_evidence(self):
        row = closeout_row(1)
        bad = settlement_row(
            row,
            status="verified_paid",
            verified="9",
            evidence=["d" * 64],
        )
        with self.assertRaisesRegex(ra.ReceivablesInputError, "verified_paid"):
            self.compile([row], [bad])

    def test_partial_requires_strictly_partial_amount_and_evidence(self):
        row = closeout_row(1)
        bad = settlement_row(
            row,
            status="partially_verified",
            verified="10",
            evidence=["e" * 64],
        )
        with self.assertRaisesRegex(
            ra.ReceivablesInputError, "partially_verified"
        ):
            self.compile([row], [bad])

    def test_payment_evidence_cannot_cross_support_items(self):
        first = closeout_row(1)
        second = closeout_row(2)
        digest = "1" * 64
        one = settlement_row(
            first, status="partially_verified", verified="1", evidence=[digest]
        )
        two = settlement_row(
            second, status="partially_verified", verified="1", evidence=[digest]
        )
        with self.assertRaisesRegex(ra.ReceivablesInputError, "cross-used"):
            self.compile([first, second], [one, two])

    def test_future_merge_time_fails_closed(self):
        row = closeout_row(1)
        row["merged_at"] = "2026-09-13T13:00:01Z"
        with self.assertRaisesRegex(ra.ReceivablesInputError, "future"):
            self.compile([row], [settlement_row(row)])

    def test_legacy_receivables_policy_is_rejected(self):
        legacy = {
            "schema": "bounty-receivables-aging-policy/v1",
            "version": 1,
            "followup_after_hours": 24,
            "escalate_after_hours": 72,
        }
        row = closeout_row(1)
        with self.assertRaises(ra.ReceivablesInputError):
            self.compile([row], [settlement_row(row)], legacy)

    def test_invalid_policy_order_fails_before_network(self):
        with self.assertRaisesRegex(ra.ReceivablesInputError, "must exceed"):
            ra.compile_receivables_review(
                [{}], [], policy(72, 24), wallet="wallet-1"
            )

    def test_authority_ceiling_is_explicit_and_integrity_enforced(self):
        row = closeout_row(1)
        receipt, *_ = self.compile([row], [settlement_row(row)])
        summary = receipt["summary"]
        for key in (
            "unverified_advertised_amount_is_receivable",
            "payable_obligation_claim",
            "debt_claim",
            "prior_contact_claim",
            "merge_used_as_cash",
            "accounting_revenue_claim",
            "autonomous_contact_authorized",
            "wallet_or_provider_mutation_authorized",
            "spend_authorized",
        ):
            self.assertFalse(summary[key])
        self.assertTrue(ra.verify_receipt_integrity(receipt))
        forged = copy.deepcopy(receipt)
        forged["summary"]["debt_claim"] = True
        core = dict(forged)
        core.pop("receipt_sha256")
        forged["receipt_sha256"] = ra._sha256(core)
        self.assertFalse(ra.verify_receipt_integrity(forged))

    def test_same_authority_snapshot_is_deterministic(self):
        row = closeout_row(1, hours=30)
        first, *_ = self.compile([row], [settlement_row(row)])
        second, *_ = self.compile([row], [settlement_row(row)])
        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )

    def test_no_public_as_of_backdate_parameter(self):
        self.assertNotIn("as_of", ra.compile_receivables_review.__annotations__)
        with self.assertRaises(TypeError):
            ra.compile_receivables_review(
                [{}], [], policy(), wallet="wallet-1", as_of=NOW
            )

    def test_manifest_root_is_strict(self):
        with self.assertRaisesRegex(ra.ReceivablesInputError, "extra=other"):
            ra._manifest_items(
                {"schema_version": 1, "items": [], "other": 1}
            )

    def test_output_refuses_existing_file_and_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            target.write_text("old")
            with self.assertRaisesRegex(
                ra.ReceivablesInputError, "refusing to overwrite"
            ):
                ra._write_output(str(target), {"ok": True})
            target.unlink()
            real = Path(tmp) / "real.json"
            real.write_text("old")
            target.symlink_to(real)
            with self.assertRaisesRegex(
                ra.ReceivablesInputError, "refusing to overwrite"
            ):
                ra._write_output(str(target), {"ok": True})
            self.assertEqual(real.read_text(), "old")


if __name__ == "__main__":
    unittest.main()
