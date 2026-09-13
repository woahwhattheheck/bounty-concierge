# SPDX-License-Identifier: MIT
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from concierge import reinvestment_allocator as ra


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def amount_text(value):
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def rate_text(cash, minutes):
    frac = Fraction(Decimal(cash)) * 60 / minutes
    with localcontext() as context:
        context.prec = 36
        value = Decimal(frac.numerator) / Decimal(frac.denominator)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def source_receipt(specs):
    items = []
    for index, spec in enumerate(sorted(specs, key=lambda s: (s[0].casefold(), s[1]))):
        repo, pr, status, cash, minutes = spec
        state = "MERGED" if status != "not_inferred" else "OPEN"
        evidence = [hashlib.sha256(f"evidence-{index}".encode()).hexdigest()] if status != "not_inferred" else []
        cash_text = amount_text(cash)
        items.append(
            {
                "repo": repo,
                "pr": pr,
                "state": state,
                "cash_status": status,
                "verified_cash_rtc": cash_text,
                "active_minutes": minutes,
                "realized_rtc_per_hour_estimate": rate_text(cash_text, minutes),
                "payment_evidence_sha256s": evidence,
            }
        )
    total_cash = sum((Decimal(row["verified_cash_rtc"]) for row in items), Decimal("0"))
    total_minutes = sum(row["active_minutes"] for row in items)
    cash_scope = [
        {
            "repo": row["repo"],
            "pr": row["pr"],
            "state": row["state"],
            "cash_status": row["cash_status"],
            "verified_cash_rtc": row["verified_cash_rtc"],
            "payment_evidence_sha256s": row["payment_evidence_sha256s"],
        }
        for row in items
    ]
    effort_scope = [
        {"repo": row["repo"], "pr": row["pr"], "active_minutes": row["active_minutes"]}
        for row in items
    ]
    ranked = sorted(
        items,
        key=lambda row: (
            -(Fraction(Decimal(row["verified_cash_rtc"])) * 60 / row["active_minutes"]),
            row["repo"].casefold(),
            row["pr"],
        ),
    )
    payload = {
        "schema_version": 1,
        "wallet": "rtc-test-wallet",
        "history_source": "captured_wallet",
        "scope_sha256": sha({"cash": cash_scope, "effort": effort_scope}),
        "summary": {
            "currency": "RTC",
            "verified_cash_total": amount_text(total_cash),
            "active_minutes_total": total_minutes,
            "realized_rtc_per_hour_estimate": rate_text(total_cash, total_minutes),
            "fully_paid_items": sum(row["cash_status"] == "verified_paid" for row in items),
            "partially_paid_items": sum(row["cash_status"] == "partially_verified" for row in items),
            "zero_verified_cash_items": sum(row["cash_status"] == "not_inferred" for row in items),
            "item_count": len(items),
            "scope_complete": True,
            "cash_basis": "revenue_settlement_wallet_evidence",
            "effort_basis": "operator_active_minutes",
            "fx_conversion": False,
            "accounting_revenue_claim": False,
            "tax_claim": False,
            "payout_or_transfer_authority": False,
        },
        "ranking": [
            {
                "rank": i,
                "repo": row["repo"],
                "pr": row["pr"],
                "realized_rtc_per_hour_estimate": row["realized_rtc_per_hour_estimate"],
            }
            for i, row in enumerate(ranked, 1)
        ],
        "items": items,
    }
    payload["receipt_sha256"] = sha(payload)
    return payload


def taxonomy(source, families):
    mappings = []
    for row in source["items"]:
        mappings.append({"repo": row["repo"], "pr": row["pr"], "family": families[(row["repo"], row["pr"])]})
    payload = {"schema": "realized-reinvestment-taxonomy/v1", "version": 1, "mappings": mappings}
    payload["taxonomy_sha256"] = sha(payload)
    return payload


def policy(min_samples=2, min_nonzero=1, threshold="1", cap_bps=7000):
    payload = {
        "schema": "realized-reinvestment-policy/v1",
        "version": 1,
        "minimum_samples": min_samples,
        "minimum_nonzero_cash_samples": min_nonzero,
        "minimum_median_rtc_per_hour": threshold,
        "maximum_family_capacity_bps": cap_bps,
    }
    payload["policy_sha256"] = sha(payload)
    return payload


class ReinvestmentAllocatorTests(unittest.TestCase):
    def setUp(self):
        self.source = source_receipt(
            [
                ("org/a", 1, "verified_paid", "10", 60),
                ("org/a", 2, "verified_paid", "20", 60),
                ("org/b", 3, "verified_paid", "5", 60),
                ("org/b", 4, "not_inferred", "0", 60),
            ]
        )
        self.taxonomy = taxonomy(
            self.source,
            {
                ("org/a", 1): "family-a",
                ("org/a", 2): "family-a",
                ("org/b", 3): "family-b",
                ("org/b", 4): "family-b",
            },
        )
        self.policy = policy(threshold="4", cap_bps=7000)

    def compile(self, capacity=100):
        return ra.compile_reinvestment_review(self.source, self.taxonomy, self.policy, capacity)

    def test_happy_path_is_realized_evidence_only(self):
        receipt = self.compile()
        self.assertEqual(receipt["schema"], "realized-reinvestment-review/v1")
        self.assertEqual(receipt["summary"]["review_state"], "READY_FOR_OWNER_REINVESTMENT_REVIEW")
        self.assertFalse(receipt["summary"]["advertised_reward_used_as_cash"])
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["family-a"]["verified_cash_rtc"], "30")
        self.assertEqual(rows["family-a"]["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(rows["family-b"]["state"], "DEPRIORITIZE_REVIEW")
        self.assertEqual(rows["family-a"]["recommended_review_capacity_minutes"], 70)
        self.assertEqual(receipt["summary"]["unallocated_capacity_minutes"], 30)

    def test_receipt_deterministic_and_verifiable(self):
        first = self.compile(137)
        second = self.compile(137)
        self.assertEqual(canonical(first), canonical(second))
        self.assertTrue(ra.verify_reinvestment_receipt(first, self.source, self.taxonomy, self.policy, 137))

    def test_receipt_tamper_fails(self):
        receipt = self.compile()
        receipt["families"][0]["verified_cash_rtc"] = "999"
        self.assertFalse(ra.verify_reinvestment_receipt(receipt, self.source, self.taxonomy, self.policy, 100))

    def test_source_receipt_digest_tamper_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["wallet"] = "changed"
        with self.assertRaises(ra.ReinvestmentInputError):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_source_scope_tamper_resealed_still_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["scope_sha256"] = "0" * 64
        body = dict(bad)
        body.pop("receipt_sha256")
        bad["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope_sha256"):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_source_summary_tamper_resealed_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["summary"]["verified_cash_total"] = "999"
        body = dict(bad)
        body.pop("receipt_sha256")
        bad["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "summary"):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_source_ranking_tamper_resealed_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["ranking"][0], bad["ranking"][1] = bad["ranking"][1], bad["ranking"][0]
        bad["ranking"][0]["rank"] = 1
        bad["ranking"][1]["rank"] = 2
        body = dict(bad)
        body.pop("receipt_sha256")
        bad["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "ranking"):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_changed_payment_evidence_resealed_source_fails_scope(self):
        bad = json.loads(json.dumps(self.source))
        bad["items"][0]["payment_evidence_sha256s"][0] = "1" * 64
        body = dict(bad)
        body.pop("receipt_sha256")
        bad["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope_sha256"):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_taxonomy_digest_tamper_fails(self):
        bad = json.loads(json.dumps(self.taxonomy))
        bad["mappings"][0]["family"] = "other"
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "taxonomy digest"):
            ra.compile_reinvestment_review(self.source, bad, self.policy, 100)

    def test_taxonomy_must_exactly_cover_source(self):
        bad = json.loads(json.dumps(self.taxonomy))
        bad["mappings"].pop()
        body = dict(bad)
        body.pop("taxonomy_sha256")
        bad["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "exactly cover"):
            ra.compile_reinvestment_review(self.source, bad, self.policy, 100)

    def test_taxonomy_duplicate_mapping_fails(self):
        bad = json.loads(json.dumps(self.taxonomy))
        bad["mappings"].append(dict(bad["mappings"][0]))
        body = dict(bad)
        body.pop("taxonomy_sha256")
        bad["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "duplicate taxonomy"):
            ra.compile_reinvestment_review(self.source, bad, self.policy, 100)

    def test_policy_digest_tamper_fails(self):
        bad = json.loads(json.dumps(self.policy))
        bad["minimum_samples"] = 99
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "policy digest"):
            ra.compile_reinvestment_review(self.source, self.taxonomy, bad, 100)

    def test_bool_cannot_alias_integer_policy(self):
        bad = json.loads(json.dumps(self.policy))
        bad["minimum_samples"] = True
        body = dict(bad)
        body.pop("policy_sha256")
        bad["policy_sha256"] = sha(body)
        with self.assertRaises(ra.ReinvestmentInputError):
            ra.compile_reinvestment_review(self.source, self.taxonomy, bad, 100)

    def test_bool_cannot_alias_capacity(self):
        with self.assertRaises(ra.ReinvestmentInputError):
            ra.compile_reinvestment_review(self.source, self.taxonomy, self.policy, True)

    def test_sample_threshold_boundary(self):
        strict = policy(min_samples=3, min_nonzero=1, threshold="1", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(self.source, self.taxonomy, strict, 100)
        self.assertTrue(all(row["state"] == "LEARN_MORE" for row in receipt["families"]))
        self.assertEqual(receipt["summary"]["review_state"], "NO_SCALE_REVIEW_ELIGIBLE")

    def test_nonzero_sample_threshold_boundary(self):
        strict = policy(min_samples=2, min_nonzero=2, threshold="1", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(self.source, self.taxonomy, strict, 100)
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["family-a"]["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(rows["family-b"]["state"], "LEARN_MORE")

    def test_median_threshold_exact_boundary_passes(self):
        exact = policy(min_samples=2, min_nonzero=2, threshold="15", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(self.source, self.taxonomy, exact, 100)
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["family-a"]["median_realized_rtc_per_hour_estimate"], "15")
        self.assertEqual(rows["family-a"]["state"], "SCALE_REVIEW_ELIGIBLE")

    def test_median_threshold_just_above_deprioritizes(self):
        exact = policy(min_samples=2, min_nonzero=2, threshold="15.0001", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(self.source, self.taxonomy, exact, 100)
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["family-a"]["state"], "DEPRIORITIZE_REVIEW")

    def test_zero_cash_family_never_scale_eligible_even_zero_threshold(self):
        source = source_receipt([
            ("org/z", 1, "not_inferred", "0", 60),
            ("org/z", 2, "not_inferred", "0", 60),
        ])
        tax = taxonomy(source, {("org/z", 1): "zero", ("org/z", 2): "zero"})
        pol = policy(min_samples=2, min_nonzero=0, threshold="0", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(source, tax, pol, 100)
        self.assertEqual(receipt["families"][0]["state"], "DEPRIORITIZE_REVIEW")
        self.assertIn("zero_total_verified_cash", receipt["families"][0]["reasons"])

    def test_partial_cash_counts_as_nonzero_evidence(self):
        source = source_receipt([
            ("org/p", 1, "partially_verified", "2", 60),
            ("org/p", 2, "verified_paid", "2", 60),
        ])
        tax = taxonomy(source, {("org/p", 1): "partial", ("org/p", 2): "partial"})
        pol = policy(min_samples=2, min_nonzero=2, threshold="2", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(source, tax, pol, 60)
        row = receipt["families"][0]
        self.assertEqual(row["partially_paid_items"], 1)
        self.assertEqual(row["nonzero_cash_samples"], 2)
        self.assertEqual(row["state"], "SCALE_REVIEW_ELIGIBLE")

    def test_capacity_cap_leaves_unallocated_when_only_one_family(self):
        receipt = self.compile(101)
        row = {r["family"]: r for r in receipt["families"]}["family-a"]
        self.assertEqual(row["recommended_review_capacity_minutes"], 70)
        self.assertEqual(receipt["summary"]["unallocated_capacity_minutes"], 31)

    def test_largest_remainder_tie_break_is_family_slug(self):
        source = source_receipt([
            ("org/a", 1, "verified_paid", "10", 60),
            ("org/b", 2, "verified_paid", "10", 60),
        ])
        tax = taxonomy(source, {("org/a", 1): "alpha", ("org/b", 2): "beta"})
        pol = policy(min_samples=1, min_nonzero=1, threshold="1", cap_bps=10000)
        receipt = ra.compile_reinvestment_review(source, tax, pol, 3)
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["alpha"]["recommended_review_capacity_minutes"], 2)
        self.assertEqual(rows["beta"]["recommended_review_capacity_minutes"], 1)

    def test_capped_redistribution(self):
        source = source_receipt([
            ("org/a", 1, "verified_paid", "100", 60),
            ("org/b", 2, "verified_paid", "10", 60),
            ("org/c", 3, "verified_paid", "10", 60),
        ])
        tax = taxonomy(source, {("org/a", 1): "alpha", ("org/b", 2): "beta", ("org/c", 3): "gamma"})
        pol = policy(min_samples=1, min_nonzero=1, threshold="1", cap_bps=5000)
        receipt = ra.compile_reinvestment_review(source, tax, pol, 100)
        rows = {row["family"]: row for row in receipt["families"]}
        self.assertEqual(rows["alpha"]["recommended_review_capacity_minutes"], 50)
        self.assertEqual(rows["beta"]["recommended_review_capacity_minutes"], 25)
        self.assertEqual(rows["gamma"]["recommended_review_capacity_minutes"], 25)
        self.assertEqual(receipt["summary"]["unallocated_capacity_minutes"], 0)

    def test_mapping_order_is_canonical_not_caller_selected(self):
        bad = json.loads(json.dumps(self.taxonomy))
        bad["mappings"].reverse()
        body = dict(bad)
        body.pop("taxonomy_sha256")
        bad["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "canonical identity order"):
            ra.compile_reinvestment_review(self.source, bad, self.policy, 100)

    def test_source_item_order_is_canonical(self):
        bad = json.loads(json.dumps(self.source))
        bad["items"].reverse()
        body = dict(bad)
        body.pop("receipt_sha256")
        bad["receipt_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "canonical identity order"):
            ra.compile_reinvestment_review(bad, self.taxonomy, self.policy, 100)

    def test_unknown_keys_fail_closed(self):
        bad = json.loads(json.dumps(self.policy))
        bad["mystery"] = 1
        body = dict(bad)
        body.pop("policy_sha256")
        bad["policy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "exact keys"):
            ra.compile_reinvestment_review(self.source, self.taxonomy, bad, 100)

    def test_family_slug_rejects_free_text(self):
        bad = json.loads(json.dumps(self.taxonomy))
        bad["mappings"][0]["family"] = "Buyer says scale this!"
        body = dict(bad)
        body.pop("taxonomy_sha256")
        bad["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "lowercase slug"):
            ra.compile_reinvestment_review(self.source, bad, self.policy, 100)

    def test_policy_drift_breaks_verification(self):
        receipt = self.compile()
        changed = policy(min_samples=2, min_nonzero=1, threshold="5", cap_bps=7000)
        self.assertFalse(ra.verify_reinvestment_receipt(receipt, self.source, self.taxonomy, changed, 100))

    def test_taxonomy_drift_breaks_verification(self):
        receipt = self.compile()
        changed = taxonomy(
            self.source,
            {("org/a", 1): "family-x", ("org/a", 2): "family-x", ("org/b", 3): "family-b", ("org/b", 4): "family-b"},
        )
        self.assertFalse(ra.verify_reinvestment_receipt(receipt, self.source, changed, self.policy, 100))

    def test_capacity_drift_breaks_verification(self):
        receipt = self.compile(100)
        self.assertFalse(ra.verify_reinvestment_receipt(receipt, self.source, self.taxonomy, self.policy, 101))

    def test_duplicate_json_key_is_rejected_by_cli_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "duplicate JSON key"):
                ra._strict_json(str(path))

    def test_output_is_create_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "refusing to overwrite"):
                ra._write_output(str(path), self.compile())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_output_refuses_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text("safe", encoding="utf-8")
            link = Path(tmp) / "receipt.json"
            os.symlink(target, link)
            with self.assertRaises(ra.ReinvestmentInputError):
                ra._write_output(str(link), self.compile())
            self.assertEqual(target.read_text(encoding="utf-8"), "safe")

    def test_optimized_python_suite_smoke(self):
        code = (
            "from tests.test_reinvestment_allocator import source_receipt,taxonomy,policy; "
            "from concierge.reinvestment_allocator import compile_reinvestment_review; "
            "s=source_receipt([('org/a',1,'verified_paid','1',60)]); "
            "t=taxonomy(s,{('org/a',1):'family-a'}); "
            "p=policy(min_samples=1,min_nonzero=1,threshold='1',cap_bps=10000); "
            "r=compile_reinvestment_review(s,t,p,5); "
            "assert r['families'][0]['recommended_review_capacity_minutes']==5"
        )
        completed = subprocess.run(
            [sys.executable, "-O", "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
