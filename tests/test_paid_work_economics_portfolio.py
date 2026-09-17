from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from concierge.paid_work_effort_value_gate import compile_paid_work_effort_value_gate
from concierge.paid_work_economics_portfolio import (
    PaidWorkPortfolioError,
    compile_bundle,
    compile_paid_work_economics_portfolio,
    digest,
    loads_strict,
    verify_bundle,
    verify_paid_work_economics_portfolio,
)
from tests.test_paid_work_effort_value_gate import candidate, request


def gate_item(
    *,
    generation: str,
    work_id: str,
    url: str,
    kind: str = "go",
) -> dict:
    item = candidate()
    item["work_id"] = work_id
    item["canonical_source_url"] = url
    if kind == "hold-value":
        item["model_tool_cost"] = {"state": "UNKNOWN"}
    elif kind == "hold-account":
        item["payout_route"] = {
            "state": "UNKNOWN",
            "evidence_url": f"https://evidence.example/{work_id}-payout",
            "observed_at": "2026-09-16T19:30:00Z",
        }
    elif kind == "skip":
        item["advertised_payout"]["amount"] = "1"
        item["model_tool_cost"] = {
            "state": "KNOWN",
            "amount": "0",
            "currency": "USD",
        }
    elif kind != "go":
        raise ValueError(kind)
    gate_request = request(item)
    receipt = compile_paid_work_effort_value_gate(gate_request)
    return {
        "candidate_generation_id": generation,
        "candidate_generation_sha256": digest(item),
        "gate_request": gate_request,
        "gate_receipt": receipt,
    }


def portfolio_input() -> dict:
    return {
        "schema": "paid-work-economics-portfolio-input/v1",
        "as_of": "2026-09-16T20:10:00Z",
        "policy": {
            "max_candidates": 20,
            "max_inventory_age_seconds": 3600,
            "max_gate_age_seconds": 3600,
        },
        "inventory": {
            "inventory_id": "paid-work-census-20260916T2005Z",
            "generation_sha256": "a" * 64,
            "captured_at": "2026-09-16T20:05:00Z",
            "complete_through": "2026-09-16T20:04:00Z",
            "complete": True,
        },
        "candidates": [
            gate_item(
                generation="gen-go",
                work_id="work-go",
                url="https://github.com/example/repo/issues/1",
                kind="go",
            ),
            gate_item(
                generation="gen-value",
                work_id="work-value",
                url="https://github.com/example/repo/issues/2",
                kind="hold-value",
            ),
            gate_item(
                generation="gen-account",
                work_id="work-account",
                url="https://github.com/example/repo/issues/3",
                kind="hold-account",
            ),
            gate_item(
                generation="gen-skip",
                work_id="work-skip",
                url="https://github.com/example/repo/issues/4",
                kind="skip",
            ),
        ],
    }


class PaidWorkEconomicsPortfolioTests(unittest.TestCase):
    def test_partitions_all_four_gate_decisions(self):
        report = compile_paid_work_economics_portfolio(portfolio_input())
        self.assertEqual(4, report["summary"]["candidate_count"])
        self.assertEqual(1, report["summary"]["go_count"])
        self.assertEqual(1, report["summary"]["hold_value_unknown_count"])
        self.assertEqual(1, report["summary"]["hold_account_gate_count"])
        self.assertEqual(1, report["summary"]["skip_economics_count"])
        self.assertEqual(
            {"GO", "HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE", "SKIP_ECONOMICS"},
            {key for key, rows in report["partitions"].items() if rows},
        )

    def test_only_go_enters_internal_implementation_queue(self):
        report = compile_paid_work_economics_portfolio(portfolio_input())
        self.assertEqual(["work-go"], [row["work_id"] for row in report["implementation_queue"]])
        row = report["implementation_queue"][0]
        self.assertTrue(row["requires_fresh_live_intake_and_availability"])
        self.assertTrue(row["internal_implementation_only"])
        self.assertFalse(row["external_authority"])
        self.assertEqual(
            "concierge.revenue_dispatch.qualify_available_live_revenue_intake",
            row["next_seam"],
        )

    def test_every_authority_remains_false(self):
        report = compile_paid_work_economics_portfolio(portfolio_input())
        self.assertTrue(report["authority"])
        self.assertTrue(all(value is False for value in report["authority"].values()))
        for rows in report["partitions"].values():
            for row in rows:
                self.assertTrue(all(value is False for value in row["authority"].values()))

    def test_hold_worklists_preserve_reason_codes_and_categories(self):
        report = compile_paid_work_economics_portfolio(portfolio_input())
        account = report["partitions"]["HOLD_ACCOUNT_GATE"][0]
        self.assertIn("PAYOUT_ROUTE_NOT_CONFIRMED", account["reason_codes"])
        self.assertIn(
            ("account_or_acceptance", "PAYOUT_ROUTE_NOT_CONFIRMED"),
            {(row["category"], row["reason_code"]) for row in account["evidence_worklist"]},
        )
        value = report["partitions"]["HOLD_VALUE_UNKNOWN"][0]
        self.assertIn("MODEL_TOOL_COST_UNKNOWN", value["reason_codes"])
        self.assertTrue(any(row["category"] == "valuation_or_economics" for row in value["evidence_worklist"]))

    def test_gate_receipt_tamper_rejected(self):
        value = portfolio_input()
        value["candidates"][0]["gate_receipt"]["decision"] = "SKIP_ECONOMICS"
        with self.assertRaisesRegex(PaidWorkPortfolioError, "replay mismatch"):
            compile_paid_work_economics_portfolio(value)

    def test_gate_request_tamper_against_receipt_rejected(self):
        value = portfolio_input()
        value["candidates"][0]["gate_request"]["candidate"]["canonical_source_url"] = (
            "https://github.com/example/repo/issues/999"
        )
        with self.assertRaisesRegex(PaidWorkPortfolioError, "replay mismatch"):
            compile_paid_work_economics_portfolio(value)

    def test_candidate_generation_digest_mismatch_rejected(self):
        value = portfolio_input()
        value["candidates"][0]["candidate_generation_sha256"] = "f" * 64
        with self.assertRaisesRegex(PaidWorkPortfolioError, "generation digest mismatch"):
            compile_paid_work_economics_portfolio(value)

    def test_duplicate_generation_id_rejected(self):
        value = portfolio_input()
        value["candidates"][1]["candidate_generation_id"] = value["candidates"][0]["candidate_generation_id"]
        with self.assertRaisesRegex(PaidWorkPortfolioError, "duplicate or replayed"):
            compile_paid_work_economics_portfolio(value)

    def test_duplicate_generation_digest_rejected(self):
        value = portfolio_input()
        clone = copy.deepcopy(value["candidates"][0])
        clone["candidate_generation_id"] = "gen-replay"
        clone["gate_request"]["candidate"]["work_id"] = "work-replay"
        clone["gate_request"]["candidate"]["canonical_source_url"] = "https://github.com/example/repo/issues/9"
        clone["gate_receipt"] = compile_paid_work_effort_value_gate(clone["gate_request"])
        # Preserve the old generation digest deliberately: this is a replayed generation claim.
        value["candidates"].append(clone)
        with self.assertRaises(PaidWorkPortfolioError):
            compile_paid_work_economics_portfolio(value)

    def test_duplicate_work_id_alias_rejected(self):
        value = portfolio_input()
        second = value["candidates"][1]
        second["gate_request"]["candidate"]["work_id"] = "work-go"
        second["candidate_generation_sha256"] = digest(second["gate_request"]["candidate"])
        second["gate_receipt"] = compile_paid_work_effort_value_gate(second["gate_request"])
        with self.assertRaisesRegex(PaidWorkPortfolioError, "duplicate work_id"):
            compile_paid_work_economics_portfolio(value)

    def test_duplicate_canonical_source_alias_rejected(self):
        value = portfolio_input()
        second = value["candidates"][1]
        second["gate_request"]["candidate"]["canonical_source_url"] = (
            value["candidates"][0]["gate_request"]["candidate"]["canonical_source_url"]
        )
        second["candidate_generation_sha256"] = digest(second["gate_request"]["candidate"])
        second["gate_receipt"] = compile_paid_work_effort_value_gate(second["gate_request"])
        with self.assertRaisesRegex(PaidWorkPortfolioError, "canonical-source alias"):
            compile_paid_work_economics_portfolio(value)

    def test_stale_gate_receipt_rejected(self):
        value = portfolio_input()
        value["as_of"] = "2026-09-16T22:00:01Z"
        value["inventory"]["captured_at"] = "2026-09-16T21:59:00Z"
        value["inventory"]["complete_through"] = "2026-09-16T21:58:00Z"
        with self.assertRaisesRegex(PaidWorkPortfolioError, "gate receipt is stale"):
            compile_paid_work_economics_portfolio(value)

    def test_future_gate_receipt_rejected(self):
        value = portfolio_input()
        value["as_of"] = "2026-09-16T19:59:59Z"
        value["inventory"]["captured_at"] = "2026-09-16T19:59:00Z"
        value["inventory"]["complete_through"] = "2026-09-16T19:58:00Z"
        with self.assertRaisesRegex(PaidWorkPortfolioError, "from the future"):
            compile_paid_work_economics_portfolio(value)

    def test_incomplete_inventory_rejected(self):
        value = portfolio_input()
        value["inventory"]["complete"] = False
        with self.assertRaisesRegex(PaidWorkPortfolioError, "incomplete"):
            compile_paid_work_economics_portfolio(value)

    def test_stale_inventory_rejected(self):
        value = portfolio_input()
        value["inventory"]["captured_at"] = "2026-09-16T18:00:00Z"
        value["inventory"]["complete_through"] = "2026-09-16T17:59:00Z"
        with self.assertRaisesRegex(PaidWorkPortfolioError, "inventory is stale"):
            compile_paid_work_economics_portfolio(value)

    def test_noncash_candidate_never_queues_without_authorized_valuation(self):
        value = portfolio_input()
        item = candidate(
            amount="25",
            currency="RTC",
            unit_type="NONCASH",
            cost="0",
            cost_currency="RTC",
        )
        item["work_id"] = "work-rtc"
        item["canonical_source_url"] = "https://github.com/example/repo/issues/10"
        req = request(item)
        value["candidates"] = [
            {
                "candidate_generation_id": "gen-rtc",
                "candidate_generation_sha256": digest(item),
                "gate_request": req,
                "gate_receipt": compile_paid_work_effort_value_gate(req),
            }
        ]
        report = compile_paid_work_economics_portfolio(value)
        self.assertEqual([], report["implementation_queue"])
        self.assertEqual(1, report["summary"]["hold_value_unknown_count"])
        self.assertIn(
            "NONCASH_VALUE_UNAUTHORIZED",
            report["partitions"]["HOLD_VALUE_UNKNOWN"][0]["reason_codes"],
        )

    def test_expired_deadline_never_queues(self):
        value = portfolio_input()
        item = candidate(deadline="2026-09-16T19:59:59Z")
        item["work_id"] = "work-expired"
        item["canonical_source_url"] = "https://github.com/example/repo/issues/11"
        req = request(item)
        value["candidates"] = [
            {
                "candidate_generation_id": "gen-expired",
                "candidate_generation_sha256": digest(item),
                "gate_request": req,
                "gate_receipt": compile_paid_work_effort_value_gate(req),
            }
        ]
        report = compile_paid_work_economics_portfolio(value)
        self.assertEqual([], report["implementation_queue"])
        self.assertEqual("SKIP_ECONOMICS", report["partitions"]["SKIP_ECONOMICS"][0]["decision"])

    def test_report_and_bundle_are_deterministic_and_verifiable(self):
        value = portfolio_input()
        left = compile_paid_work_economics_portfolio(value)
        right = compile_paid_work_economics_portfolio(copy.deepcopy(value))
        self.assertEqual(left, right)
        self.assertTrue(verify_paid_work_economics_portfolio(value, left))
        bundle_left = compile_bundle(value)
        bundle_right = compile_bundle(copy.deepcopy(value))
        self.assertEqual(bundle_left, bundle_right)
        self.assertTrue(verify_bundle(value, *bundle_left))

    def test_report_tamper_rejected(self):
        value = portfolio_input()
        report = compile_paid_work_economics_portfolio(value)
        report["authority"]["external_claim_authority"] = True
        with self.assertRaises(PaidWorkPortfolioError):
            verify_paid_work_economics_portfolio(value, report)

    def test_bundle_tamper_rejected(self):
        value = portfolio_input()
        report, markdown, receipt = compile_bundle(value)
        tampered = bytearray(report)
        tampered[-2] = ord(" ")
        with self.assertRaisesRegex(PaidWorkPortfolioError, "bundle mismatch"):
            verify_bundle(value, bytes(tampered), markdown, receipt)

    def test_float_and_duplicate_json_are_rejected(self):
        with self.assertRaisesRegex(PaidWorkPortfolioError, "non-integer"):
            loads_strict('{"x":1.25}')
        with self.assertRaisesRegex(PaidWorkPortfolioError, "duplicate JSON key"):
            loads_strict('{"x":1,"x":2}')

    def test_cli_compile_verify_and_refuse_overwrite(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.json"
            out = Path(td) / "out"
            source.write_text(json.dumps(portfolio_input(), sort_keys=True), encoding="utf-8")
            base = [sys.executable, "-m", "concierge.paid_work_economics_portfolio"]
            compiled = subprocess.run(
                base + ["compile", str(source), "--out-dir", str(out)],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            self.assertIn("COMPILED", compiled.stdout)
            verified = subprocess.run(
                base
                + [
                    "verify",
                    str(source),
                    "--report",
                    str(out / "portfolio.json"),
                    "--markdown",
                    str(out / "portfolio.md"),
                    "--receipt",
                    str(out / "receipt.json"),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(0, verified.returncode, verified.stderr)
            self.assertIn("VERIFIED", verified.stdout)
            second = subprocess.run(
                base + ["compile", str(source), "--out-dir", str(out)],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(2, second.returncode)


if __name__ == "__main__":
    unittest.main()
