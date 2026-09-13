from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from concierge.contract_qualification import ContractQualificationInputError
from concierge import external_contract_ranker as ranker


ZERO = "0" * 64


def receipt(source: str, currency: str = "USD", amount: str = "100", *, q=None):
    return {
        "schema_version": "bounty-concierge.external-contract-qualification-receipt/v1",
        "disposition": "ACTIONABLE",
        "status": "READY_FOR_OWNER_SUBMISSION",
        "bid_ready": True,
        "submitted": False,
        "owner_action_required": True,
        "canonical_source_url": source,
        "source_digest": hashlib.sha256((source + "|source").encode()).hexdigest(),
        "native_currency": currency,
        "bid": {"currency": currency, "amount": amount, "delivery_days": 7},
        "submission_route": "MANUAL_OWNER",
        "unproven_claim_ids": [],
        "reason_codes": [],
        "evidence_bindings": [],
        "qualification_digest": q or hashlib.sha256((source + "|qualification").encode()).hexdigest(),
        "as_of": "2026-09-13T12:00:00Z",
        "authority": {
            "marketplace_submission": False,
            "terms_acceptance": False,
            "account_creation": False,
            "kyc_completion": False,
            "spend": False,
            "contract_acceptance": False,
            "work_awarded": False,
            "payment_received": False,
            "revenue_recognized": False,
        },
    }


def candidate(source: str, currency: str = "USD", amount: str = "100", *, effort="10", probability="0.5"):
    return {
        "snapshot": {"source": source},
        "qualification_receipt": receipt(source, currency, amount),
        "estimated_effort_hours": effort,
        "estimated_award_probability": probability,
    }


def good_verify(snapshot, rec, *, as_of):
    return {
        "valid": True,
        "qualification_digest": rec["qualification_digest"],
        "status": rec["status"],
        "current_disposition": "ACTIONABLE",
        "verified_as_of": as_of,
        "submitted": False,
    }


class ExternalContractRankerTests(unittest.TestCase):
    def setUp(self):
        self.verify_patch = mock.patch.object(ranker, "verify_contract_qualification_receipt", side_effect=good_verify)
        self.verify = self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)

    def rank(self, rows):
        return ranker.rank_external_contracts(rows, as_of="2026-09-13T12:40:00Z")

    def test_partitions_arbitrary_native_currencies_without_global_winner(self):
        result = self.rank([
            candidate("https://market.example/a", "USD", "500", effort="10", probability="0.5"),
            candidate("https://market.example/b", "CAD", "700", effort="20", probability="0.5"),
            candidate("https://market.example/c", "EUR", "300", effort="5", probability="0.5"),
        ])
        self.assertEqual(list(result["partitions"]), ["CAD", "EUR", "USD"])
        self.assertEqual(result["ranked_count"], 3)
        self.assertIsNone(result["global_winner"])
        self.assertFalse(result["authority"]["fx_conversion"])
        self.assertFalse(result["authority"]["cross_currency_ranking"])

    def test_ranking_uses_exact_fraction_arithmetic(self):
        rows = [
            candidate("https://market.example/lower", "USD", "1", effort="1", probability="0.333333333332"),
            candidate("https://market.example/higher", "USD", "1", effort="1", probability="0.333333333333"),
        ]
        result = self.rank(rows)
        ranked = result["partitions"]["USD"]["ranked"]
        self.assertEqual([row["canonical_source_url"] for row in ranked], [
            "https://market.example/higher", "https://market.example/lower"
        ])
        self.assertEqual(ranked[0]["estimated_ev_per_hour"], "0.333333333333")

    def test_reverification_happens_before_estimates_influence_ranking(self):
        self.verify_patch.stop()
        def verifier(snapshot, rec, *, as_of):
            if rec["canonical_source_url"].endswith("stale"):
                raise ContractQualificationInputError("stale")
            return good_verify(snapshot, rec, as_of=as_of)
        patch = mock.patch.object(ranker, "verify_contract_qualification_receipt", side_effect=verifier)
        patch.start(); self.addCleanup(patch.stop)
        result = self.rank([
            candidate("https://market.example/stale", amount="999999", effort="1", probability="1"),
            candidate("https://market.example/fresh", amount="10", effort="10", probability="0.1"),
        ])
        self.assertEqual(result["ranked_count"], 1)
        self.assertEqual(result["partitions"]["USD"]["ranked"][0]["canonical_source_url"], "https://market.example/fresh")
        self.assertEqual(result["excluded"][0]["reason_code"], "QUALIFICATION_INVALID_OR_STALE")

    def test_current_hold_is_not_rankable(self):
        self.verify_patch.stop()
        patch = mock.patch.object(
            ranker,
            "verify_contract_qualification_receipt",
            return_value={"valid": True, "current_disposition": "HOLD"},
        )
        patch.start(); self.addCleanup(patch.stop)
        result = self.rank([candidate("https://market.example/a")])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(result["excluded"][0]["reason_code"], "QUALIFICATION_INVALID_OR_STALE")

    def test_duplicate_source_fence_precedes_estimate_validation(self):
        valid = candidate("https://market.example/same", effort="2")
        malformed = candidate("https://market.example/same", effort=True)
        result = self.rank([valid, malformed])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual([row["reason_code"] for row in result["excluded"]], [
            "DUPLICATE_CANONICAL_SOURCE", "DUPLICATE_CANONICAL_SOURCE"
        ])

    def test_duplicate_source_across_conflicting_currency_receipts_is_fenced(self):
        result = self.rank([
            candidate("https://market.example/same", "USD", "10"),
            candidate("https://market.example/same", "EUR", "10"),
        ])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual(len(result["excluded"]), 2)

    def test_float_and_bool_estimates_fail_closed(self):
        result = self.rank([
            candidate("https://market.example/float", effort=1.5),
            candidate("https://market.example/bool", probability=True),
            candidate("https://market.example/good"),
        ])
        self.assertEqual(result["ranked_count"], 1)
        self.assertEqual([row["reason_code"] for row in result["excluded"]], ["ESTIMATE_INVALID", "ESTIMATE_INVALID"])

    def test_probability_bounds_and_positive_effort(self):
        result = self.rank([
            candidate("https://market.example/negative", probability="-0.1"),
            candidate("https://market.example/high", probability="1.0001"),
            candidate("https://market.example/zero", effort="0"),
        ])
        self.assertEqual(result["ranked_count"], 0)
        self.assertTrue(all(row["reason_code"] == "ESTIMATE_INVALID" for row in result["excluded"]))

    def test_qualification_identity_is_bound_into_public_row(self):
        row = candidate("https://market.example/a", "GBP", "125.50")
        result = self.rank([row])
        ranked = result["partitions"]["GBP"]["ranked"][0]
        self.assertEqual(ranked["source_digest"], row["qualification_receipt"]["source_digest"])
        self.assertEqual(ranked["qualification_digest"], row["qualification_receipt"]["qualification_digest"])
        self.assertEqual(ranked["proposed_bid_amount"], "125.5")
        self.assertEqual(ranked["authority"]["bid"], "proposed_not_awarded")
        for key in ("fx_conversion", "marketplace_submission", "contract_acceptance", "work_awarded", "payment_received", "revenue_recognized", "spend"):
            self.assertIs(ranked["authority"][key], False)

    def test_bad_currency_or_digest_after_verification_still_fails_closed(self):
        bad_currency = candidate("https://market.example/currency")
        bad_currency["qualification_receipt"]["native_currency"] = "usd"
        bad_currency["qualification_receipt"]["bid"]["currency"] = "usd"
        bad_digest = candidate("https://market.example/digest")
        bad_digest["qualification_receipt"]["source_digest"] = "A" * 64
        result = self.rank([bad_currency, bad_digest])
        self.assertEqual(result["ranked_count"], 0)
        self.assertEqual([row["reason_code"] for row in result["excluded"]], [
            "QUALIFICATION_INVALID_OR_STALE", "QUALIFICATION_INVALID_OR_STALE"
        ])

    def test_candidate_shape_is_exact(self):
        row = candidate("https://market.example/a")
        row["surprise"] = "field"
        result = self.rank([row])
        self.assertEqual(result["excluded"], [{
            "input_index": 0, "canonical_source_url": None, "reason_code": "CANDIDATE_SHAPE_INVALID"
        }])
        self.verify.assert_not_called()

    def test_candidate_count_bound(self):
        with self.assertRaises(ranker.ExternalContractRankInputError):
            self.rank([{}] * 501)

    def test_as_of_is_canonical_utc(self):
        with self.assertRaises(ranker.ExternalContractRankInputError):
            ranker.rank_external_contracts([], as_of="2026-09-13T08:40:00-04:00")
        with self.assertRaises(ranker.ExternalContractRankInputError):
            ranker.rank_external_contracts([], as_of="2026-09-13T12:40:00.123Z")

    def test_ranking_digest_is_deterministic_and_estimate_sensitive(self):
        first = self.rank([candidate("https://market.example/a", effort="10")])
        second = self.rank([candidate("https://market.example/a", effort="10")])
        changed = self.rank([candidate("https://market.example/a", effort="11")])
        self.assertEqual(first["ranking_digest"], second["ranking_digest"])
        self.assertNotEqual(first["ranking_digest"], changed["ranking_digest"])
        self.assertRegex(first["ranking_digest"], r"^[0-9a-f]{64}$")

    def test_ties_are_stable_by_source_not_input_order(self):
        a = candidate("https://market.example/a", amount="100")
        b = candidate("https://market.example/b", amount="100")
        first = self.rank([b, a])
        second = self.rank([a, b])
        self.assertEqual(
            [row["canonical_source_url"] for row in first["partitions"]["USD"]["ranked"]],
            ["https://market.example/a", "https://market.example/b"],
        )
        self.assertEqual(
            [row["canonical_source_url"] for row in second["partitions"]["USD"]["ranked"]],
            ["https://market.example/a", "https://market.example/b"],
        )

    def test_strict_json_rejects_duplicate_keys_and_nonstandard_constants(self):
        with self.assertRaises(ranker.ExternalContractRankInputError):
            ranker.loads_strict_json('{"candidates":[],"candidates":[]}')
        with self.assertRaises(ranker.ExternalContractRankInputError):
            ranker.loads_strict_json('{"x":NaN}')

    def test_regular_file_reader_rejects_duplicate_key_request(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "request.json"
            path.write_text('{"candidates":[],"candidates":[]}', encoding="utf-8")
            with self.assertRaises(ranker.ExternalContractRankInputError):
                ranker._read_request(str(path))

    @unittest.skipUnless(hasattr(os, "symlink") and getattr(os, "O_NOFOLLOW", 0), "requires O_NOFOLLOW")
    def test_request_file_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target.json"
            link = Path(temp) / "request.json"
            target.write_text('{"candidates":[]}', encoding="utf-8")
            os.symlink(target, link)
            with self.assertRaises(ranker.ExternalContractRankInputError):
                ranker._read_request(str(link))

    def test_summary_states_no_global_winner_or_fx(self):
        result = self.rank([candidate("https://market.example/a", "USD")])
        summary = ranker.format_summary(result)
        self.assertIn("global_winner=none", summary)
        self.assertIn("fx_conversion=false", summary)

    def test_public_authority_ceiling_is_all_nonoperational(self):
        result = self.rank([candidate("https://market.example/a")])
        authority = result["authority"]
        for key in (
            "fx_conversion", "cross_currency_ranking", "marketplace_submission",
            "terms_acceptance", "contract_acceptance", "work_awarded",
            "payment_received", "revenue_recognized", "spend",
        ):
            self.assertIs(authority[key], False)

    def test_current_verification_receives_trusted_as_of_for_every_candidate(self):
        self.rank([
            candidate("https://market.example/a"),
            candidate("https://market.example/b"),
        ])
        self.assertEqual(self.verify.call_count, 2)
        self.assertEqual(
            [call.kwargs["as_of"] for call in self.verify.call_args_list],
            ["2026-09-13T12:40:00Z", "2026-09-13T12:40:00Z"],
        )


if __name__ == "__main__":
    unittest.main()
