from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from concierge import cash_cycle_review as ccr

UTC = timezone.utc
AS_OF = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
WALLET = "wallet-zch"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def row_hash(row):
    return ccr._payout.history_row_sha256(row, wallet=WALLET)


def transfer(at: datetime, tx: str, *, status="confirmed"):
    return {
        "type": "transfer_in",
        "from": "payer-wallet",
        "to": WALLET,
        "amount": "10",
        "tx_hash": tx,
        "status": status,
        "timestamp": iso(at),
    }


def fixture(items):
    """Build closeout/history/bindings. items=(repo, pr, merge, [rows])."""
    closeout_items, rows, bind_items = [], [], []
    for repo, pr, merged_at, these_rows in items:
        closeout_items.append({
            "repo": repo,
            "pr": pr,
            "state": "MERGED",
            "currency": "RTC",
            "merged_at": iso(merged_at),
        })
        rows.extend(these_rows)
        bind_items.append({
            "repo": repo,
            "pr": pr,
            "history_sha256s": [row_hash(row) for row in these_rows],
        })
    closeout = {"schema_version": 1, "items": closeout_items}
    history = {
        "schema_version": 1,
        "source": "rustchain_wallet_history",
        "wallet": WALLET,
        "items": rows,
    }
    bindings = {"schema_version": 1, "items": bind_items}
    return closeout, history, bindings


def fake_compile(closeout, history, bindings, *, as_of):
    by_key = {(x["repo"].casefold(), x["pr"]): x for x in bindings["items"]}
    history_by_hash = {row_hash(row): row for row in history["items"]}
    output = []
    for item in closeout["items"]:
        selected = by_key[(item["repo"].casefold(), item["pr"])]["history_sha256s"]
        selected_rows = [history_by_hash[h] for h in selected]
        statuses = [row.get("status", "confirmed") for row in selected_rows]
        if any(s == "failed" for s in statuses):
            action = "owner_review_failed_transfer"
        elif any(s in {"pending", "confirming"} for s in statuses):
            action = "monitor_pending_confirmation"
        elif any(s == "confirmed" for s in statuses):
            action = "run_revenue_settlement"
        else:
            action = "await_transfer_initiation_window"
        output.append({
            "repo": item["repo"],
            "pr": item["pr"],
            "state": item["state"],
            "currency": item["currency"],
            "merged_at": item["merged_at"],
            "action": action,
            "bound_history_sha256s": list(selected),
        })
    receipt = {
        "schema_version": 1,
        "as_of": iso(as_of),
        "wallet": WALLET,
        "history_source": "captured_wallet",
        "items": output,
        "cash_authority": "none",
        "external_action_authority": "none",
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    return receipt


def policy(samples=1, hours=24):
    return {"schema_version": 1, "minimum_confirmed_samples": samples,
            "review_lag_hours": hours}


class CashCycleReviewTests(unittest.TestCase):
    def compile(self, sources, pol=None, at=AS_OF):
        closeout, history, bindings = sources
        with mock.patch.object(ccr._payout, "compile_payout_escalation", side_effect=fake_compile):
            return ccr.compile_cash_cycle_review(
                closeout, history, bindings, pol or policy(), evaluated_at=at)

    def verify(self, receipt, sources, pol=None, at=None):
        closeout, history, bindings = sources
        with mock.patch.object(ccr._payout, "compile_payout_escalation", side_effect=fake_compile):
            return ccr.verify_cash_cycle_review(
                receipt, closeout, history, bindings, pol or policy(),
                verified_at=at or AS_OF)

    def test_confirmed_happy_path_exact_lag_and_authority(self):
        merge = AS_OF - timedelta(hours=48)
        sources = fixture([("Org/Repo", 7, merge, [transfer(merge + timedelta(hours=30), "tx1")])])
        receipt = self.compile(sources, policy(1, 24))
        self.assertEqual(receipt["observations"][0]["last_confirmed_transfer_lag_seconds"], 30 * 3600)
        repo = receipt["repositories"][0]
        self.assertEqual(repo["repo"], "org/repo")
        self.assertEqual(repo["state"], "READY_FOR_OWNER_CASH_CYCLE_REVIEW")
        self.assertEqual(repo["observed_merged_item_count"], 1)
        self.assertFalse(receipt["authority"]["payer_or_sponsor_identity_inference"])
        self.assertFalse(receipt["authority"]["revenue_recognition"])
        self.assertTrue(self.verify(receipt, sources, policy(1, 24)))

    def test_pending_excluded_but_repo_is_insufficient(self):
        merge = AS_OF - timedelta(hours=10)
        sources = fixture([("Org/Repo", 1, merge, [transfer(merge + timedelta(hours=1), "txp", status="pending")])])
        receipt = self.compile(sources)
        self.assertEqual(receipt["observations"], [])
        repo = receipt["repositories"][0]
        self.assertEqual(repo["state"], "INSUFFICIENT_CONFIRMED_HISTORY")
        self.assertEqual(repo["confirmed_sample_count"], 0)
        self.assertIsNone(repo["median_last_confirmed_transfer_lag_seconds"])

    def test_failed_excluded(self):
        merge = AS_OF - timedelta(hours=10)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=2), "txf", status="failed")])])
        self.assertEqual(self.compile(sources)["observations"], [])

    def test_no_transfer_excluded(self):
        merge = AS_OF - timedelta(hours=10)
        sources = fixture([("a/b", 1, merge, [])])
        receipt = self.compile(sources)
        self.assertEqual(receipt["repositories"][0]["confirmed_sample_count"], 0)

    def test_multiple_confirmed_rows_first_last(self):
        merge = AS_OF - timedelta(hours=50)
        rows = [transfer(merge + timedelta(hours=5), "a"),
                transfer(merge + timedelta(hours=20), "b")]
        receipt = self.compile(fixture([("a/b", 2, merge, rows)]))
        obs = receipt["observations"][0]
        self.assertEqual(obs["first_confirmed_transfer_lag_seconds"], 5 * 3600)
        self.assertEqual(obs["last_confirmed_transfer_lag_seconds"], 20 * 3600)
        self.assertEqual(obs["confirmed_transfer_evidence_count"], 2)
        self.assertEqual(obs["cash_claim"], "none_from_cash_cycle_review")

    def test_threshold_exact_boundary_triggers(self):
        merge = AS_OF - timedelta(hours=30)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=24), "a")])])
        receipt = self.compile(sources, policy(1, 24))
        self.assertEqual(receipt["repositories"][0]["state"], "READY_FOR_OWNER_CASH_CYCLE_REVIEW")

    def test_below_threshold_no_trigger(self):
        merge = AS_OF - timedelta(hours=30)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=23), "a")])])
        receipt = self.compile(sources, policy(1, 24))
        self.assertEqual(receipt["repositories"][0]["state"], "OBSERVED_NO_REVIEW_TRIGGER")

    def test_minimum_sample_gate(self):
        merge = AS_OF - timedelta(hours=30)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=29), "a")])])
        receipt = self.compile(sources, policy(2, 24))
        self.assertEqual(receipt["repositories"][0]["state"], "INSUFFICIENT_CONFIRMED_HISTORY")

    def test_even_median_is_exact_half_second(self):
        m1 = AS_OF - timedelta(hours=50)
        m2 = AS_OF - timedelta(hours=50)
        r1 = transfer(m1 + timedelta(seconds=10), "a")
        r2 = transfer(m2 + timedelta(seconds=11), "b")
        sources = fixture([("a/b", 1, m1, [r1]), ("a/b", 2, m2, [r2])])
        receipt = self.compile(sources, policy(2, 1))
        self.assertEqual(receipt["repositories"][0]["median_last_confirmed_transfer_lag_seconds"], "10.5")

    def test_odd_median(self):
        merge = AS_OF - timedelta(days=2)
        items = []
        for pr, seconds in [(1, 10), (2, 30), (3, 20)]:
            items.append(("a/b", pr, merge, [transfer(merge + timedelta(seconds=seconds), str(pr))]))
        receipt = self.compile(fixture(items), policy(3, 1))
        self.assertEqual(receipt["repositories"][0]["median_last_confirmed_transfer_lag_seconds"], "20")

    def test_repo_partitioning(self):
        merge = AS_OF - timedelta(days=2)
        sources = fixture([
            ("a/b", 1, merge, [transfer(merge + timedelta(hours=3), "a")]),
            ("c/d", 2, merge, [transfer(merge + timedelta(hours=40), "b")]),
        ])
        receipt = self.compile(sources, policy(1, 24))
        self.assertEqual([r["repo"] for r in receipt["repositories"]], ["a/b", "c/d"])
        self.assertEqual([r["state"] for r in receipt["repositories"]],
                         ["OBSERVED_NO_REVIEW_TRIGGER", "READY_FOR_OWNER_CASH_CYCLE_REVIEW"])

    def test_casefold_repo_is_one_partition(self):
        merge = AS_OF - timedelta(days=2)
        sources = fixture([
            ("A/B", 1, merge, [transfer(merge + timedelta(hours=3), "a")]),
            ("a/b", 2, merge, [transfer(merge + timedelta(hours=4), "b")]),
        ])
        receipt = self.compile(sources, policy(2, 24))
        self.assertEqual(len(receipt["repositories"]), 1)
        self.assertEqual(receipt["repositories"][0]["source_prs"], [1, 2])

    def test_analytics_order_independent_source_custody_stays_exact(self):
        merge = AS_OF - timedelta(days=2)
        items = [
            ("a/b", 1, merge, [transfer(merge + timedelta(hours=3), "a")]),
            ("c/d", 2, merge, [transfer(merge + timedelta(hours=4), "b")]),
        ]
        one = self.compile(fixture(items))
        two = self.compile(fixture(list(reversed(items))))
        self.assertEqual(one["observations"], two["observations"])
        self.assertEqual(one["repositories"], two["repositories"])
        self.assertEqual(one["authority"], two["authority"])
        # Exact source-list ordering is part of custody even though analytics are stable.
        self.assertNotEqual(one["source"]["closeout_sha256"],
                            two["source"]["closeout_sha256"])

    def test_policy_bool_rejected(self):
        merge = AS_OF - timedelta(days=2)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=1), "a")])])
        with self.assertRaises(ccr.CashCycleInputError):
            self.compile(sources, {"schema_version": 1, "minimum_confirmed_samples": True,
                                   "review_lag_hours": 24})
        with self.assertRaises(ccr.CashCycleInputError):
            self.compile(sources, {"schema_version": 1, "minimum_confirmed_samples": 1,
                                   "review_lag_hours": False})

    def test_policy_bounds(self):
        merge = AS_OF - timedelta(days=2)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=1), "a")])])
        for samples, hours in [(0, 24), (1, 0), (10001, 24), (1, 43801)]:
            with self.assertRaises(ccr.CashCycleInputError):
                self.compile(sources, policy(samples, hours))

    def test_nonconfirmed_row_cannot_hide_behind_fake_run_settlement(self):
        merge = AS_OF - timedelta(days=2)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=1), "a", status="pending")])])
        def lying(*args, **kwargs):
            r = fake_compile(*args, **kwargs)
            r["items"][0]["action"] = "run_revenue_settlement"
            r["receipt_sha256"] = hashlib.sha256(canonical({k:v for k,v in r.items() if k != "receipt_sha256"})).hexdigest()
            return r
        with mock.patch.object(ccr._payout, "compile_payout_escalation", side_effect=lying):
            with self.assertRaises(ccr.CashCycleEvidenceError):
                ccr.compile_cash_cycle_review(*sources, policy(), evaluated_at=AS_OF)

    def test_transfer_before_merge_rejected_defensively(self):
        merge = AS_OF - timedelta(hours=10)
        sources = fixture([("a/b", 1, merge, [transfer(merge - timedelta(seconds=1), "a")])])
        with self.assertRaises(ccr.CashCycleEvidenceError):
            self.compile(sources)

    def test_future_transfer_rejected_defensively(self):
        merge = AS_OF - timedelta(hours=1)
        sources = fixture([("a/b", 1, merge, [transfer(AS_OF + timedelta(seconds=1), "a")])])
        with self.assertRaises(ccr.CashCycleEvidenceError):
            self.compile(sources)

    def test_duplicate_history_rows_rejected(self):
        merge = AS_OF - timedelta(hours=5)
        row = transfer(merge + timedelta(hours=1), "a")
        closeout, history, bindings = fixture([("a/b", 1, merge, [row])])
        history["items"].append(deepcopy(row))
        with self.assertRaises(ccr.CashCycleEvidenceError):
            self.compile((closeout, history, bindings))

    def test_receipt_tamper_fails_verifier(self):
        merge = AS_OF - timedelta(hours=5)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=2), "a")])])
        receipt = self.compile(sources)
        tampered = deepcopy(receipt)
        tampered["repositories"][0]["state"] = "READY_FOR_OWNER_CASH_CYCLE_REVIEW"
        self.assertFalse(self.verify(tampered, sources))

    def test_source_drift_fails_verifier(self):
        merge = AS_OF - timedelta(hours=5)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=2), "a")])])
        receipt = self.compile(sources)
        drift = deepcopy(sources)
        drift[1]["items"][0]["amount"] = "11"
        self.assertFalse(self.verify(receipt, drift))

    def test_verifier_time_rollback_fails(self):
        merge = AS_OF - timedelta(hours=5)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=2), "a")])])
        receipt = self.compile(sources)
        self.assertFalse(self.verify(receipt, sources, at=AS_OF - timedelta(seconds=1)))

    def test_receipt_source_digests_present(self):
        merge = AS_OF - timedelta(hours=5)
        sources = fixture([("a/b", 1, merge, [transfer(merge + timedelta(hours=2), "a")])])
        receipt = self.compile(sources)
        for value in receipt["source"].values():
            self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_strict_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text('{"a":1,"a":2}', encoding="utf-8")
            with self.assertRaises(ccr.CashCycleInputError):
                ccr._load_json(str(p))

    def test_nonstandard_nan_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text('{"a":NaN}', encoding="utf-8")
            with self.assertRaises(ccr.CashCycleInputError):
                ccr._load_json(str(p))

    def test_output_create_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.json"
            ccr._write_exclusive(str(p), {"ok": True})
            self.assertEqual(json.loads(p.read_text()), {"ok": True})
            with self.assertRaises(ccr.CashCycleInputError):
                ccr._write_exclusive(str(p), {"ok": True})

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_output_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target"
            target.write_text("safe", encoding="utf-8")
            link = Path(td) / "link"
            os.symlink(target, link)
            with self.assertRaises(ccr.CashCycleInputError):
                ccr._write_exclusive(str(link), {"x": 1})
            self.assertEqual(target.read_text(), "safe")

    def test_upstream_exception_is_not_swallowed(self):
        sources = fixture([])
        with mock.patch.object(ccr._payout, "compile_payout_escalation", side_effect=RuntimeError("authority red")):
            with self.assertRaisesRegex(RuntimeError, "authority red"):
                ccr.compile_cash_cycle_review(*sources, policy(), evaluated_at=AS_OF)

    @unittest.skipUnless(hasattr(ccr._payout, "policy_receipt"),
                         "real payout_escalation module unavailable in isolated local harness")
    def test_real_payout_escalation_integration(self):
        merge = AS_OF - timedelta(hours=30)
        row = transfer(merge + timedelta(hours=25), "real-integration")
        closeout = {
            "schema_version": 1,
            "items": [{
                "repo": "a/b",
                "pr": 9,
                "canonical_url": "https://github.com/a/b/pull/9",
                "head_sha": "a" * 40,
                "advertised_amount": "10",
                "currency": "RTC",
                "state": "MERGED",
                "merged_at": iso(merge),
                "next_action": "route_settlement_followup",
                "reason": "merged_without_settlement_followup_evidence",
                "settlement_followup_url": None,
                "cash_status": "not_inferred",
            }],
        }
        history = {
            "schema_version": 1,
            "source": "rustchain_wallet_history",
            "wallet": WALLET,
            "items": [row],
        }
        bindings = {
            "schema_version": 1,
            "policy_version": ccr._payout.policy_receipt()["policy_version"],
            "wallet": WALLET,
            "closeout_sha256": ccr._payout.closeout_snapshot_sha256(closeout),
            "history_capture_sha256": ccr._payout.history_capture_sha256(history),
            "items": [{
                "repo": "a/b",
                "pr": 9,
                "history_sha256s": [ccr._payout.history_row_sha256(row, wallet=WALLET)],
            }],
        }
        receipt = ccr.compile_cash_cycle_review(
            closeout, history, bindings, policy(1, 24), evaluated_at=AS_OF)
        self.assertEqual(receipt["repositories"][0]["state"],
                         "READY_FOR_OWNER_CASH_CYCLE_REVIEW")
        self.assertEqual(receipt["observations"][0]["last_confirmed_transfer_lag_seconds"],
                         25 * 3600)


if __name__ == "__main__":
    unittest.main()
