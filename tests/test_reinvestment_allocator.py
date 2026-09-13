# SPDX-License-Identifier: MIT
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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


def economics_receipt(specs, wallet="wallet-1", history_source="queried_wallet"):
    items = []
    for index, spec in enumerate(sorted(specs, key=lambda row: (row[0].casefold(), row[1]))):
        repo, pr, status, cash, minutes = spec
        state = "MERGED" if status != "not_inferred" else "OPEN"
        evidence = [hashlib.sha256(f"evidence-{index}".encode()).hexdigest()] if status != "not_inferred" else []
        cash_text = amount_text(cash)
        items.append({"repo": repo, "pr": pr, "state": state, "cash_status": status, "verified_cash_rtc": cash_text, "active_minutes": minutes, "realized_rtc_per_hour_estimate": rate_text(cash_text, minutes), "payment_evidence_sha256s": evidence})
    total_cash = sum((Decimal(row["verified_cash_rtc"]) for row in items), Decimal("0"))
    total_minutes = sum(row["active_minutes"] for row in items)
    cash_scope = [{"repo": row["repo"], "pr": row["pr"], "state": row["state"], "cash_status": row["cash_status"], "verified_cash_rtc": row["verified_cash_rtc"], "payment_evidence_sha256s": row["payment_evidence_sha256s"]} for row in items]
    effort_scope = [{"repo": row["repo"], "pr": row["pr"], "active_minutes": row["active_minutes"]} for row in items]
    ranked = sorted(items, key=lambda row: (-(Fraction(Decimal(row["verified_cash_rtc"])) * 60 / row["active_minutes"]), row["repo"].casefold(), row["pr"]))
    payload = {
        "schema_version": 1,
        "wallet": wallet,
        "history_source": history_source,
        "scope_sha256": sha({"cash": cash_scope, "effort": effort_scope}),
        "summary": {"currency": "RTC", "verified_cash_total": amount_text(total_cash), "active_minutes_total": total_minutes, "realized_rtc_per_hour_estimate": rate_text(total_cash, total_minutes), "fully_paid_items": sum(row["cash_status"] == "verified_paid" for row in items), "partially_paid_items": sum(row["cash_status"] == "partially_verified" for row in items), "zero_verified_cash_items": sum(row["cash_status"] == "not_inferred" for row in items), "item_count": len(items), "scope_complete": True, "cash_basis": "revenue_settlement_wallet_evidence", "effort_basis": "operator_active_minutes", "fx_conversion": False, "accounting_revenue_claim": False, "tax_claim": False, "payout_or_transfer_authority": False},
        "ranking": [{"rank": position, "repo": row["repo"], "pr": row["pr"], "realized_rtc_per_hour_estimate": row["realized_rtc_per_hour_estimate"]} for position, row in enumerate(ranked, 1)],
        "items": items,
    }
    payload["receipt_sha256"] = sha(payload)
    return payload


def taxonomy(source, family_by_identity):
    mappings = [{"repo": row["repo"], "pr": row["pr"], "family": family_by_identity[(row["repo"], row["pr"])]} for row in source["items"]]
    payload = {"schema": "realized-reinvestment-taxonomy/v1", "version": 1, "mappings": mappings}
    payload["taxonomy_sha256"] = sha(payload)
    return payload


def policy(min_samples=2, min_nonzero=1, threshold="1", cap_bps=7000):
    payload = {"schema": "realized-reinvestment-policy/v1", "version": 1, "minimum_samples": min_samples, "minimum_nonzero_cash_samples": min_nonzero, "minimum_median_rtc_per_hour": threshold, "maximum_family_capacity_bps": cap_bps}
    payload["policy_sha256"] = sha(payload)
    return payload


class ReinvestmentAllocatorV2Tests(unittest.TestCase):
    def setUp(self):
        self.source = economics_receipt([("org/a", 1, "verified_paid", "10", 60), ("org/a", 2, "verified_paid", "20", 60), ("org/b", 3, "verified_paid", "5", 60), ("org/b", 4, "not_inferred", "0", 60)])
        self.taxonomy = taxonomy(self.source, {("org/a", 1): "family-a", ("org/a", 2): "family-a", ("org/b", 3): "family-b", ("org/b", 4): "family-b"})
        self.policy = policy(threshold="4", cap_bps=7000)
        self.manifest = [{"repo": "scope/x", "pr": 1}]
        self.bindings = [{"repo": "scope/x", "pr": 1, "binding": "opaque"}]
        self.effort = {"schema_version": 1, "source": "operator_active_minutes", "items": []}
        self.p_closeout = patch.object(ra.closeout, "build_closeout_queue", return_value=[{"provider": "github", "state": "live"}])
        self.p_history = patch.object(ra.settlement, "_query_canonical_history", return_value=([{"provider": "wallet", "state": "live"}], "wallet-1"))
        self.p_compile = patch.object(ra.rue, "compile_realized_unit_economics", return_value=self.source)
        self.p_verify = patch.object(ra.rue, "verify_receipt", return_value=True)
        self.m_closeout = self.p_closeout.start()
        self.m_history = self.p_history.start()
        self.m_compile = self.p_compile.start()
        self.m_verify = self.p_verify.start()
        self.addCleanup(self.p_closeout.stop)
        self.addCleanup(self.p_history.stop)
        self.addCleanup(self.p_compile.stop)
        self.addCleanup(self.p_verify.stop)

    def compile(self, capacity=100, **kwargs):
        return ra.compile_reinvestment_review(self.manifest, self.bindings, self.effort, self.taxonomy, self.policy, capacity, wallet="wallet-1", **kwargs)

    def test_public_path_reacquires_both_provider_authorities(self):
        result = self.compile()
        self.m_closeout.assert_called_once_with(self.manifest, max_pages=10)
        self.m_history.assert_called_once_with("wallet-1")
        call = self.m_compile.call_args
        self.assertEqual(call.kwargs["history_source"], "queried_wallet")
        self.assertEqual(call.kwargs["wallet"], "wallet-1")
        self.assertEqual(call.kwargs["history_wallet"], "wallet-1")
        self.assertEqual(result["authority"]["cash_evidence_authority"], "reacquired_not_inherited")

    def test_standalone_receipt_and_history_are_not_public_inputs(self):
        params = inspect.signature(ra.compile_reinvestment_review).parameters
        self.assertNotIn("realized_receipt", params)
        self.assertNotIn("history", params)
        self.assertNotIn("history_source", params)
        with self.assertRaises(TypeError):
            ra.compile_reinvestment_review(self.source, self.taxonomy, self.policy, 100)

    def test_fully_self_consistent_forged_receipt_has_no_entry_point(self):
        forged = economics_receipt([("org/forged", 99, "verified_paid", "999999", 1)])
        self.assertTrue(ra._verify_digest_envelope(forged, "receipt_sha256", "forged"))
        params = inspect.signature(ra.compile_reinvestment_review).parameters
        self.assertNotIn("receipt", params)
        self.assertFalse(any(name in params for name in ("source", "economics_receipt")))

    def test_provider_closeout_failure_blocks_before_wallet(self):
        self.m_closeout.side_effect = OSError("github unavailable")
        with self.assertRaises(OSError):
            self.compile()
        self.m_history.assert_not_called()
        self.m_compile.assert_not_called()

    def test_provider_wallet_failure_blocks_before_economics(self):
        self.m_history.side_effect = OSError("wallet unavailable")
        with self.assertRaises(OSError):
            self.compile()
        self.m_compile.assert_not_called()

    def test_wallet_identity_mismatch_fails_closed(self):
        self.m_history.return_value = ([], "other-wallet")
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "identity mismatch"):
            self.compile()
        self.m_compile.assert_not_called()

    def test_captured_history_from_compiler_is_rejected(self):
        self.m_compile.return_value = economics_receipt([("org/a", 1, "verified_paid", "10", 60), ("org/a", 2, "verified_paid", "20", 60), ("org/b", 3, "verified_paid", "5", 60), ("org/b", 4, "not_inferred", "0", 60)], history_source="captured_wallet")
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "requires queried_wallet"):
            self.compile()

    def test_in_process_receipt_self_integrity_failure_blocks(self):
        self.m_verify.return_value = False
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "failed self-integrity"):
            self.compile()

    def test_source_digest_tamper_from_compiler_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["receipt_sha256"] = "0" * 64
        self.m_compile.return_value = bad
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "digest"):
            self.compile()

    def test_source_scope_tamper_resealed_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["scope_sha256"] = "0" * 64
        body = dict(bad); body.pop("receipt_sha256"); bad["receipt_sha256"] = sha(body)
        self.m_compile.return_value = bad
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "scope_sha256"):
            self.compile()

    def test_source_ranking_tamper_resealed_fails(self):
        bad = json.loads(json.dumps(self.source))
        bad["ranking"][0], bad["ranking"][1] = bad["ranking"][1], bad["ranking"][0]
        bad["ranking"][0]["rank"] = 1; bad["ranking"][1]["rank"] = 2
        body = dict(bad); body.pop("receipt_sha256"); bad["receipt_sha256"] = sha(body)
        self.m_compile.return_value = bad
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "ranking"):
            self.compile()

    def test_original_economics_object_is_not_reread_after_snapshot(self):
        original = self.source
        original_digest = self.source["receipt_sha256"]
        def mutate_verify(candidate):
            candidate["items"][0]["verified_cash_rtc"] = "999"
            original["items"][0]["verified_cash_rtc"] = "777"
            return True
        self.m_verify.side_effect = mutate_verify
        result = self.compile()
        family_a = {r["family"]: r for r in result["families"]}["family-a"]
        self.assertEqual(family_a["verified_cash_rtc"], "30")
        self.assertEqual(result["source_economics_receipt_sha256"], original_digest)

    def test_caller_taxonomy_is_frozen_before_provider_read(self):
        original_family = self.taxonomy["mappings"][0]["family"]
        def mutate_during_closeout(manifest, max_pages):
            self.taxonomy["mappings"][0]["family"] = "attacker-family"
            self.taxonomy["taxonomy_sha256"] = "0" * 64
            return [{"provider": "github"}]
        self.m_closeout.side_effect = mutate_during_closeout
        result = self.compile()
        families = {row["family"] for row in result["families"]}
        self.assertIn(original_family, families)
        self.assertNotIn("attacker-family", families)

    def test_caller_manifest_is_frozen_before_provider_receives_it(self):
        observed = {}
        def observe(manifest, max_pages):
            observed["manifest"] = manifest
            self.manifest[0]["repo"] = "mutated/after-freeze"
            return [{"provider": "github"}]
        self.m_closeout.side_effect = observe
        self.compile()
        self.assertEqual(observed["manifest"][0]["repo"], "scope/x")

    def test_planning_digests_are_not_claimed_as_owner_authentication(self):
        authority = self.compile()["authority"]
        self.assertFalse(authority["taxonomy_policy_authenticated_as_owner"])
        self.assertTrue(authority["taxonomy_policy_digests_are_integrity_only"])
        self.assertEqual(authority["taxonomy_policy_basis"], "caller_supplied_planning_inputs")

    def test_happy_path_family_states_and_capacity(self):
        result = self.compile(100)
        rows = {row["family"]: row for row in result["families"]}
        self.assertEqual(rows["family-a"]["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(rows["family-b"]["state"], "DEPRIORITIZE_REVIEW")
        self.assertEqual(rows["family-a"]["recommended_review_capacity_minutes"], 70)
        self.assertEqual(result["summary"]["unallocated_capacity_minutes"], 30)
        self.assertEqual(result["summary"]["review_state"], "READY_FOR_OWNER_REINVESTMENT_REVIEW")

    def test_sample_threshold_learn_more(self):
        self.policy = policy(min_samples=3, min_nonzero=1, threshold="1", cap_bps=10000)
        self.assertTrue(all(row["state"] == "LEARN_MORE" for row in self.compile()["families"]))

    def test_nonzero_sample_threshold(self):
        self.policy = policy(min_samples=2, min_nonzero=2, threshold="1", cap_bps=10000)
        rows = {row["family"]: row for row in self.compile()["families"]}
        self.assertEqual(rows["family-a"]["state"], "SCALE_REVIEW_ELIGIBLE")
        self.assertEqual(rows["family-b"]["state"], "LEARN_MORE")

    def test_exact_median_threshold_passes(self):
        self.policy = policy(min_samples=2, min_nonzero=2, threshold="15", cap_bps=10000)
        row = {r["family"]: r for r in self.compile()["families"]}["family-a"]
        self.assertEqual(row["median_realized_rtc_per_hour_estimate"], "15")
        self.assertEqual(row["state"], "SCALE_REVIEW_ELIGIBLE")

    def test_median_just_above_deprioritizes(self):
        self.policy = policy(min_samples=2, min_nonzero=2, threshold="15.0001", cap_bps=10000)
        row = {r["family"]: r for r in self.compile()["families"]}["family-a"]
        self.assertEqual(row["state"], "DEPRIORITIZE_REVIEW")

    def test_zero_cash_family_never_scales(self):
        source = economics_receipt([("org/z", 1, "not_inferred", "0", 60), ("org/z", 2, "not_inferred", "0", 60)])
        self.m_compile.return_value = source
        self.taxonomy = taxonomy(source, {("org/z", 1): "zero", ("org/z", 2): "zero"})
        self.policy = policy(min_samples=2, min_nonzero=0, threshold="0", cap_bps=10000)
        row = self.compile()["families"][0]
        self.assertEqual(row["state"], "DEPRIORITIZE_REVIEW")
        self.assertIn("zero_total_verified_cash", row["reasons"])

    def test_partial_cash_counts_as_nonzero(self):
        source = economics_receipt([("org/p", 1, "partially_verified", "2", 60), ("org/p", 2, "verified_paid", "2", 60)])
        self.m_compile.return_value = source
        self.taxonomy = taxonomy(source, {("org/p", 1): "partial", ("org/p", 2): "partial"})
        self.policy = policy(min_samples=2, min_nonzero=2, threshold="2", cap_bps=10000)
        row = self.compile()["families"][0]
        self.assertEqual(row["nonzero_cash_samples"], 2)
        self.assertEqual(row["partially_paid_items"], 1)
        self.assertEqual(row["state"], "SCALE_REVIEW_ELIGIBLE")

    def test_largest_remainder_tie_break_by_slug(self):
        source = economics_receipt([("org/a", 1, "verified_paid", "10", 60), ("org/b", 2, "verified_paid", "10", 60)])
        self.m_compile.return_value = source
        self.taxonomy = taxonomy(source, {("org/a", 1): "alpha", ("org/b", 2): "beta"})
        self.policy = policy(min_samples=1, min_nonzero=1, threshold="1", cap_bps=10000)
        rows = {row["family"]: row for row in self.compile(3)["families"]}
        self.assertEqual(rows["alpha"]["recommended_review_capacity_minutes"], 2)
        self.assertEqual(rows["beta"]["recommended_review_capacity_minutes"], 1)

    def test_cap_redistributes_and_conserves(self):
        source = economics_receipt([("org/a", 1, "verified_paid", "100", 60), ("org/b", 2, "verified_paid", "10", 60), ("org/c", 3, "verified_paid", "10", 60)])
        self.m_compile.return_value = source
        self.taxonomy = taxonomy(source, {("org/a", 1): "alpha", ("org/b", 2): "beta", ("org/c", 3): "gamma"})
        self.policy = policy(min_samples=1, min_nonzero=1, threshold="1", cap_bps=5000)
        result = self.compile(100); rows = {row["family"]: row for row in result["families"]}
        self.assertEqual(rows["alpha"]["recommended_review_capacity_minutes"], 50)
        self.assertEqual(rows["beta"]["recommended_review_capacity_minutes"], 25)
        self.assertEqual(rows["gamma"]["recommended_review_capacity_minutes"], 25)
        self.assertEqual(result["summary"]["unallocated_capacity_minutes"], 0)

    def test_taxonomy_digest_tamper_fails_before_allocation(self):
        self.taxonomy["mappings"][0]["family"] = "changed"
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "taxonomy digest"):
            self.compile()

    def test_taxonomy_must_exactly_cover_live_scope(self):
        self.taxonomy["mappings"].pop(); body = dict(self.taxonomy); body.pop("taxonomy_sha256"); self.taxonomy["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "exactly cover"):
            self.compile()

    def test_taxonomy_mapping_order_is_canonical(self):
        self.taxonomy["mappings"].reverse(); body = dict(self.taxonomy); body.pop("taxonomy_sha256"); self.taxonomy["taxonomy_sha256"] = sha(body)
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "canonical identity order"):
            self.compile()

    def test_policy_digest_tamper_fails(self):
        self.policy["minimum_samples"] = 99
        with self.assertRaisesRegex(ra.ReinvestmentInputError, "policy digest"):
            self.compile()

    def test_bool_capacity_and_pages_fail_closed(self):
        with self.assertRaises(ra.ReinvestmentInputError):
            self.compile(True)
        with self.assertRaises(ra.ReinvestmentInputError):
            self.compile(max_closeout_pages=True)

    def test_current_verifier_reacquires_provider_and_accepts_same_generation(self):
        receipt = self.compile(100)
        self.m_closeout.reset_mock(); self.m_history.reset_mock(); self.m_compile.reset_mock(); self.m_verify.reset_mock()
        self.m_compile.return_value = economics_receipt([("org/a", 1, "verified_paid", "10", 60), ("org/a", 2, "verified_paid", "20", 60), ("org/b", 3, "verified_paid", "5", 60), ("org/b", 4, "not_inferred", "0", 60)])
        self.m_verify.return_value = True
        self.assertTrue(ra.verify_reinvestment_receipt_current(receipt, self.manifest, self.bindings, self.effort, self.taxonomy, self.policy, 100, wallet="wallet-1"))
        self.m_closeout.assert_called_once(); self.m_history.assert_called_once()

    def test_current_verifier_rejects_provider_drift(self):
        receipt = self.compile(100)
        self.m_compile.return_value = economics_receipt([("org/a", 1, "verified_paid", "1", 60), ("org/a", 2, "verified_paid", "1", 60), ("org/b", 3, "verified_paid", "1", 60), ("org/b", 4, "not_inferred", "0", 60)])
        self.assertFalse(ra.verify_reinvestment_receipt_current(receipt, self.manifest, self.bindings, self.effort, self.taxonomy, self.policy, 100, wallet="wallet-1"))

    def test_current_verifier_rejects_receipt_tamper(self):
        receipt = self.compile(100); receipt["families"][0]["verified_cash_rtc"] = "999"
        self.assertFalse(ra.verify_reinvestment_receipt_current(receipt, self.manifest, self.bindings, self.effort, self.taxonomy, self.policy, 100, wallet="wallet-1"))

    def test_integrity_only_helper_is_explicitly_not_current_verifier(self):
        receipt = self.compile(); self.assertTrue(ra.verify_receipt_integrity_only(receipt)); receipt["authority"]["cash_evidence_authority"] = "invented"; self.assertFalse(ra.verify_receipt_integrity_only(receipt))

    def test_strict_file_loader_rejects_duplicate_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"; path.write_text('{"a":1,"a":2}', encoding="utf-8")
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "duplicate JSON"):
                ra._load_json(str(path))

    def test_strict_file_loader_rejects_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"; path.write_text('{"a":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "non-finite"):
                ra._load_json(str(path))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_strict_file_loader_rejects_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"; target.write_text("{}", encoding="utf-8"); link = Path(tmp) / "link.json"; os.symlink(target, link)
            with self.assertRaises(ra.ReinvestmentInputError):
                ra._load_json(str(link))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_strict_file_loader_rejects_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "pipe"; os.mkfifo(fifo)
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "regular file"):
                ra._load_json(str(fifo))

    def test_output_is_create_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"; path.write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(ra.ReinvestmentInputError, "refusing"):
                ra._write_output(str(path), self.compile())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_output_refuses_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"; target.write_text("safe", encoding="utf-8"); link = Path(tmp) / "receipt.json"; os.symlink(target, link)
            with self.assertRaises(ra.ReinvestmentInputError):
                ra._write_output(str(link), self.compile())
            self.assertEqual(target.read_text(encoding="utf-8"), "safe")

    def test_cli_has_no_history_or_receipt_compile_input(self):
        with self.assertRaises(SystemExit):
            ra.main(["compile", "m.json", "b.json", "e.json", "t.json", "p.json", "--wallet", "wallet-1", "--capacity-minutes", "60", "--history", "forged.json"])

    def test_max_closeout_pages_is_forwarded(self):
        self.compile(max_closeout_pages=7)
        self.m_closeout.assert_called_once_with(self.manifest, max_pages=7)

    def test_receipt_schema_and_authority_ceiling(self):
        result = self.compile(); self.assertEqual(result["schema"], "realized-reinvestment-review/v2"); authority = result["authority"]
        for key in ("future_revenue_claimed", "guaranteed_return_claimed", "autonomous_claim_authorized", "submission_authorized", "spend_authorized", "external_contact_authorized", "payment_or_wallet_mutation_authorized", "accounting_or_tax_claim"):
            self.assertFalse(authority[key])

    def test_python_optimized_smoke(self):
        code = "import ast, pathlib; p=pathlib.Path('concierge/reinvestment_allocator.py'); ast.parse(p.read_text(), feature_version=(3,9)); print('ok')"
        completed = subprocess.run([sys.executable, "-O", "-c", code], cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
