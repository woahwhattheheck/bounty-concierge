from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge.reward_settlement_ledger import (
    LedgerInputError,
    LedgerVerificationError,
    compile_bytes,
    compile_document,
    load_json_bytes,
    render_markdown,
    verify_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "reward_settlement_ledger.synthetic.json"


def document():
    return load_json_bytes(FIXTURE.read_bytes())


def source(source_id, ref, observed, authority, sha="a" * 64):
    return {
        "source_id": source_id,
        "source_ref": ref,
        "source_sha256": sha,
        "observed_at": observed,
        "authority": authority,
    }


class RewardSettlementLedgerTests(unittest.TestCase):
    def test_fixture_preserves_distinct_commercial_truths(self):
        ledger = compile_document(document())
        by_id = {row["case_id"]: row for row in ledger["records"]}
        self.assertEqual(by_id["synthetic-advertised-only"]["settlement_state"], "MERGED_UNSETTLED")
        self.assertFalse(by_id["synthetic-advertised-only"]["truth"]["provider_payment_evidenced"])
        self.assertEqual(by_id["synthetic-award-ticket-rail"]["settlement_state"], "PAYOUT_RAIL_SUPPLIED")
        self.assertFalse(by_id["synthetic-award-ticket-rail"]["truth"]["provider_payment_evidenced"])
        self.assertEqual(by_id["synthetic-pending-transfer"]["settlement_state"], "TRANSFER_EVIDENCED")
        self.assertFalse(by_id["synthetic-pending-transfer"]["truth"]["provider_payment_evidenced"])
        self.assertEqual(by_id["synthetic-paid"]["settlement_state"], "PAID_CONFIRMED")
        self.assertTrue(by_id["synthetic-paid"]["truth"]["provider_payment_evidenced"])
        self.assertEqual(by_id["synthetic-closed-no-reward"]["settlement_state"], "CLOSED_WITHOUT_REWARD")

    def test_aggregates_keep_reference_award_and_paid_separate(self):
        ledger = compile_document(document())
        aggregates = ledger["aggregates"]
        self.assertEqual(aggregates["advertised_reference_by_currency"], {"USD": 39000})
        self.assertEqual(aggregates["sponsor_awarded_by_currency"], {"USD": 36500})
        self.assertEqual(aggregates["paid_confirmed_by_currency"], {"USD": 9000})
        self.assertEqual(aggregates["recognized_revenue_by_currency"], {})
        self.assertFalse(ledger["authority"]["recognize_accounting_revenue"])

    def test_ticket_and_rail_never_become_payment(self):
        ledger = compile_document(document())
        row = next(r for r in ledger["records"] if r["case_id"] == "synthetic-award-ticket-rail")
        self.assertEqual(row["payout_ticket"]["status"], "PAYOUT_TICKET_OPENED")
        self.assertEqual(row["payout_rail"]["status"], "PAYOUT_RAIL_SUPPLIED")
        self.assertEqual(row["paid_confirmed_by_currency"], {})

    def test_merge_without_events_is_unsettled_not_paid(self):
        doc = document()
        doc["cases"] = [copy.deepcopy(doc["cases"][0])]
        doc["cases"][0]["events"] = []
        ledger = compile_document(doc)
        row = ledger["records"][0]
        self.assertEqual(row["settlement_state"], "MERGED_UNSETTLED")
        self.assertFalse(row["truth"]["merge_proves_payment"])
        self.assertFalse(row["truth"]["provider_payment_evidenced"])

    def test_nonterminal_transfer_never_becomes_paid(self):
        ledger = compile_document(document())
        row = next(r for r in ledger["records"] if r["case_id"] == "synthetic-pending-transfer")
        self.assertEqual(row["transfers"][0]["status"], "PENDING")
        self.assertEqual(row["paid_confirmed_by_currency"], {})

    def test_terminal_transfer_progression_is_paid_evidence(self):
        ledger = compile_document(document())
        row = next(r for r in ledger["records"] if r["case_id"] == "synthetic-paid")
        self.assertEqual(row["transfers"][0]["status"], "CONFIRMED")
        self.assertEqual(row["paid_confirmed_by_currency"], {"USD": 9000})

    def test_closure_is_never_inferred_from_silence(self):
        doc = document()
        doc["cases"] = [copy.deepcopy(doc["cases"][-1])]
        doc["cases"][0]["events"] = [e for e in doc["cases"][0]["events"] if e["kind"] != "CLOSURE"]
        ledger = compile_document(doc)
        row = ledger["records"][0]
        self.assertNotEqual(row["settlement_state"], "CLOSED_WITHOUT_REWARD")
        self.assertEqual(row["closure"]["status"], "CLOSURE_NOT_EVIDENCED")

    def test_closure_conflicts_with_confirmed_payment(self):
        doc = document()
        paid = copy.deepcopy(next(c for c in doc["cases"] if c["case_id"] == "synthetic-paid"))
        paid["events"].append({
            "event_id": "paid-close",
            "kind": "CLOSURE",
            "source": source("src-paid-close", "fixture://close/paid", "2026-09-17T00:07:00Z", "SPONSOR", "b" * 64),
            "reason": "NO_REWARD",
        })
        doc["cases"] = [paid]
        with self.assertRaisesRegex(LedgerInputError, "conflicts with confirmed incoming payment"):
            compile_document(doc)

    def test_conflicting_advertised_money_fails_closed(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][0])
        case["events"].append({
            "event_id": "alpha-ad-conflict",
            "kind": "ADVERTISED_BOUNTY",
            "source": source("src-alpha-ad-conflict", "fixture://offer/alpha/revision", "2026-09-17T00:02:00Z", "OFFICIAL_OFFER", "b" * 64),
            "amount_minor": 6000,
            "currency": "USD",
        })
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "conflicting advertised bounty"):
            compile_document(doc)

    def test_conflicting_eligibility_fails_closed(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][1])
        case["events"].append({
            "event_id": "bravo-ineligible",
            "kind": "ELIGIBILITY",
            "source": source("src-bravo-ineligible", "fixture://eligibility/bravo/revision", "2026-09-17T00:06:00Z", "SPONSOR", "b" * 64),
            "decision": "INELIGIBLE",
        })
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "conflicting eligibility"):
            compile_document(doc)

    def test_transfer_amount_drift_fails_closed(self):
        doc = document()
        case = copy.deepcopy(next(c for c in doc["cases"] if c["case_id"] == "synthetic-paid"))
        confirm = next(e for e in case["events"] if e["event_id"] == "delta-transfer-confirmed")
        confirm["amount_minor"] = 8999
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "changed amount or currency"):
            compile_document(doc)

    def test_transfer_terminal_conflict_fails_closed(self):
        doc = document()
        case = copy.deepcopy(next(c for c in doc["cases"] if c["case_id"] == "synthetic-paid"))
        case["events"].append({
            "event_id": "delta-transfer-failed-late",
            "kind": "TRANSFER",
            "source": source("src-delta-transfer-failed-late", "fixture://provider/delta/failed", "2026-09-17T00:07:00Z", "PROVIDER", "b" * 64),
            "transfer_id": "transfer-delta-1",
            "status": "FAILED",
            "direction": "INCOMING",
            "amount_minor": 9000,
            "currency": "USD",
        })
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "changed after terminal state"):
            compile_document(doc)

    def test_transfer_regression_fails_closed(self):
        doc = document()
        case = copy.deepcopy(next(c for c in doc["cases"] if c["case_id"] == "synthetic-pending-transfer"))
        pending = next(e for e in case["events"] if e["kind"] == "TRANSFER")
        pending["status"] = "CONFIRMING"
        case["events"].append({
            "event_id": "charlie-transfer-regress",
            "kind": "TRANSFER",
            "source": source("src-charlie-transfer-regress", "fixture://provider/charlie/regress", "2026-09-17T00:06:00Z", "PROVIDER", "b" * 64),
            "transfer_id": "transfer-charlie-1",
            "status": "PENDING",
            "direction": "INCOMING",
            "amount_minor": 20000,
            "currency": "USD",
        })
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "regressed"):
            compile_document(doc)

    def test_transfer_id_cannot_be_reused_across_work_items(self):
        doc = document()
        a = copy.deepcopy(doc["cases"][2])
        b = copy.deepcopy(doc["cases"][3])
        next(e for e in b["events"] if e["kind"] == "TRANSFER")["transfer_id"] = "transfer-charlie-1"
        for e in b["events"]:
            if e["kind"] == "TRANSFER":
                e["transfer_id"] = "transfer-charlie-1"
        doc["cases"] = [a, b]
        with self.assertRaisesRegex(LedgerInputError, "reused across work items"):
            compile_document(doc)

    def test_ticket_id_cannot_be_reused_across_work_items(self):
        doc = document()
        a = copy.deepcopy(doc["cases"][1])
        b = copy.deepcopy(doc["cases"][2])
        next(e for e in b["events"] if e["kind"] == "PAYOUT_TICKET")["ticket_id"] = "ticket-bravo-1"
        doc["cases"] = [a, b]
        with self.assertRaisesRegex(LedgerInputError, "payout ticket"):
            compile_document(doc)

    def test_reminted_source_identity_is_refused(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][0])
        original = case["events"][0]
        remint = copy.deepcopy(original)
        remint["event_id"] = "alpha-ad-remint"
        remint["source"]["source_id"] = "src-alpha-ad-reminted"
        case["events"].append(remint)
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "reminted"):
            compile_document(doc)

    def test_same_source_id_cannot_be_rebound(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][1])
        e = copy.deepcopy(case["events"][0])
        e["event_id"] = "bravo-second"
        e["source"]["source_ref"] = "fixture://different"
        case["events"].append(e)
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "rebound"):
            compile_document(doc)

    def test_wrong_source_authority_is_refused(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][3])
        transfer = next(e for e in case["events"] if e["kind"] == "TRANSFER")
        transfer["source"]["authority"] = "OFFICIAL_OFFER"
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "cannot support this fact"):
            compile_document(doc)

    def test_outgoing_transfer_is_refused(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][3])
        next(e for e in case["events"] if e["kind"] == "TRANSFER")["direction"] = "OUTGOING"
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "only incoming"):
            compile_document(doc)

    def test_bool_and_float_money_are_refused(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][0])
        case["events"][0]["amount_minor"] = True
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "positive integer"):
            compile_document(doc)
        raw = b'{"schema":"bounty-concierge/reward-settlement-input/v1","generated_at":"2026-09-17T00:30:00Z","cases":[],"x":1.5}'
        with self.assertRaisesRegex(LedgerInputError, "floating-point"):
            load_json_bytes(raw)

    def test_duplicate_json_key_and_bom_are_refused(self):
        with self.assertRaisesRegex(LedgerInputError, "duplicate JSON key"):
            load_json_bytes(b'{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(LedgerInputError, "BOM"):
            load_json_bytes(b"\xef\xbb\xbf{}")

    def test_future_source_is_refused(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][0])
        case["events"][0]["source"]["observed_at"] = "2026-09-17T00:31:00Z"
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "after generated_at"):
            compile_document(doc)

    def test_merge_source_cannot_predate_merge(self):
        doc = document()
        case = copy.deepcopy(doc["cases"][0])
        case["work"]["source"]["observed_at"] = "2026-09-16T22:59:59Z"
        doc["cases"] = [case]
        with self.assertRaisesRegex(LedgerInputError, "may not predate"):
            compile_document(doc)

    def test_semantic_order_is_deterministic(self):
        doc = document()
        forward = compile_document(copy.deepcopy(doc))
        reverse = copy.deepcopy(doc)
        reverse["cases"].reverse()
        for case in reverse["cases"]:
            case["events"].reverse()
        backward = compile_document(reverse)
        self.assertEqual(forward, backward)
        self.assertEqual(render_markdown(forward), render_markdown(backward))

    def test_receipt_verifier_rejects_each_tampered_artifact(self):
        raw = FIXTURE.read_bytes()
        ledger, markdown, receipt = compile_bytes(raw)
        verify_bytes(raw, ledger, markdown, receipt)
        for which in range(3):
            parts = [ledger, markdown, receipt]
            parts[which] = parts[which] + b"x"
            with self.assertRaises(LedgerVerificationError):
                verify_bytes(raw, *parts)

    def test_receipt_binds_exact_source_bytes(self):
        raw = FIXTURE.read_bytes()
        ledger, markdown, receipt = compile_bytes(raw)
        altered = raw + b"\n"
        with self.assertRaises(LedgerVerificationError):
            verify_bytes(altered, ledger, markdown, receipt)

    def test_cli_compile_verify_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "compiled"
            cmd = [sys.executable, "-m", "concierge.reward_settlement_ledger", "compile", "--input", str(FIXTURE), "--out-dir", str(out)]
            subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
            verify = [
                sys.executable,
                "-m",
                "concierge.reward_settlement_ledger",
                "verify",
                "--input",
                str(FIXTURE),
                "--ledger",
                str(out / "ledger.json"),
                "--markdown",
                str(out / "ledger.md"),
                "--receipt",
                str(out / "receipt.json"),
            ]
            subprocess.run(verify, cwd=ROOT, check=True, capture_output=True, text=True)
            duplicate = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(duplicate.returncode, 0)


if __name__ == "__main__":
    unittest.main()
