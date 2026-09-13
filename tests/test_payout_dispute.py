# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from concierge.payout_dispute import (
    PayoutDisputeInputError,
    compile_payout_dispute,
    load_json,
    verify_payout_dispute,
)


def payload() -> dict:
    return {
        "schema": "bounty-payout-dispute-input/v1",
        "sponsor_name": "Example Sponsor",
        "work": {
            "repo": "example/project",
            "pr": 42,
            "canonical_url": "https://github.com/example/project/pull/42",
            "head_sha": "a" * 40,
            "state": "MERGED",
            "advertised_amount": "100",
            "currency": "USD",
        },
        "acceptance": {
            "kind": "AWARDED",
            "accepted_amount": "90",
            "currency": "USD",
            "evidence_ref": "https://example.com/award/42",
            "evidence_sha256": "b" * 64,
        },
        "settlement": {
            "amount_received": "70",
            "currency": "USD",
            "transaction_id": "txn-123",
            "settled_at": "2026-09-13T14:00:00Z",
            "evidence_ref": "https://example.com/settlement/txn-123",
            "evidence_sha256": "c" * 64,
        },
        "payout_route": {
            "type": "PAYMENT_LINK",
            "value": "https://pay.example.com/bryce",
        },
    }


class PayoutDisputeTests(unittest.TestCase):
    def test_shortfall_uses_accepted_not_advertised_amount(self) -> None:
        result = compile_payout_dispute(payload())
        self.assertEqual(
            result["disposition"], "READY_FOR_OWNER_UNDERPAYMENT_REVIEW"
        )
        self.assertEqual(result["amounts"]["advertised_amount"], "100")
        self.assertEqual(result["amounts"]["accepted_amount"], "90")
        self.assertEqual(result["amounts"]["received_amount"], "70")
        self.assertEqual(result["amounts"]["difference_amount"], "20")
        self.assertIn("Accepted amount: 90 USD", result["body"])
        self.assertIn("Observed difference: 20 USD", result["body"])
        self.assertNotIn("Observed difference: 30 USD", result["body"])

    def test_accepted_less_than_advertised_can_be_fully_settled(self) -> None:
        p = payload()
        p["settlement"]["amount_received"] = "90"
        result = compile_payout_dispute(p)
        self.assertEqual(result["disposition"], "NO_SHORTFALL")
        self.assertEqual(result["amounts"]["difference_amount"], "0")
        self.assertIsNone(result["subject"])
        self.assertIsNone(result["body"])
        self.assertFalse(result["authority"]["owner_review_required_before_contact"])

    def test_zero_settlement_is_valid_and_reports_full_shortfall(self) -> None:
        p = payload()
        p["settlement"]["amount_received"] = "0"
        result = compile_payout_dispute(p)
        self.assertEqual(
            result["disposition"], "READY_FOR_OWNER_UNDERPAYMENT_REVIEW"
        )
        self.assertEqual(result["amounts"]["difference_amount"], "90")

    def test_overpayment_never_grants_refund_or_retention_authority(self) -> None:
        p = payload()
        p["settlement"]["amount_received"] = "91"
        result = compile_payout_dispute(p)
        self.assertEqual(
            result["disposition"], "OVERPAYMENT_REQUIRES_OWNER_REVIEW"
        )
        self.assertEqual(result["amounts"]["difference_amount"], "1")
        self.assertIsNone(result["body"])
        self.assertFalse(result["authority"]["refund_or_return_authority"])
        self.assertFalse(result["authority"]["payment_mutation"])
        self.assertTrue(result["authority"]["owner_review_required_before_contact"])

    def test_currency_mismatch_holds_without_fx_difference(self) -> None:
        p = payload()
        p["settlement"]["currency"] = "EUR"
        result = compile_payout_dispute(p)
        self.assertEqual(result["disposition"], "HOLD_CURRENCY_MISMATCH")
        self.assertIsNone(result["amounts"]["difference_amount"])
        self.assertIsNone(result["amounts"]["difference_currency"])
        self.assertFalse(result["authority"]["fx_conversion_permitted"])
        self.assertIsNone(result["body"])
        self.assertTrue(result["authority"]["owner_review_required_before_contact"])

    def test_acceptance_currency_may_differ_from_advertised_without_claim(self) -> None:
        p = payload()
        p["acceptance"]["accepted_amount"] = "80"
        p["acceptance"]["currency"] = "EUR"
        p["settlement"]["amount_received"] = "80"
        p["settlement"]["currency"] = "EUR"
        result = compile_payout_dispute(p)
        self.assertEqual(result["disposition"], "NO_SHORTFALL")
        self.assertEqual(result["amounts"]["advertised_currency"], "USD")
        self.assertEqual(result["amounts"]["accepted_currency"], "EUR")

    def test_merge_alone_is_not_acceptance(self) -> None:
        p = payload()
        p["acceptance"]["kind"] = "NONE"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_non_merged_work_is_rejected(self) -> None:
        p = payload()
        p["work"]["state"] = "OPEN"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_canonical_pr_url_is_bound_to_repo_and_number(self) -> None:
        p = payload()
        p["work"]["canonical_url"] = "https://github.com/example/project/pull/43"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_uppercase_head_sha_is_rejected(self) -> None:
        p = payload()
        p["work"]["head_sha"] = "A" * 40
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_bad_evidence_digest_is_rejected(self) -> None:
        for section in ("acceptance", "settlement"):
            with self.subTest(section=section):
                p = payload()
                p[section]["evidence_sha256"] = "x" * 64
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_credentialed_or_fragment_evidence_url_is_rejected(self) -> None:
        bad = (
            "https://user:pass@example.com/evidence",
            "https://example.com/evidence#fragment",
            "http://example.com/evidence",
        )
        for url in bad:
            with self.subTest(url=url):
                p = payload()
                p["settlement"]["evidence_ref"] = url
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_urls_reject_embedded_whitespace(self) -> None:
        for field_path in (("settlement", "evidence_ref"), ("payout_route", "value")):
            with self.subTest(field_path=field_path):
                p = payload()
                section, field = field_path
                p[section][field] = "https://example.com/a b"
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_payment_link_must_be_https_and_credential_free(self) -> None:
        p = payload()
        p["payout_route"]["value"] = "http://pay.example.com/bryce"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_non_payment_link_route_is_bounded_text_not_interpreted_as_url(self) -> None:
        p = payload()
        p["payout_route"] = {"type": "HOSTED_HANDLE", "value": "@bryce"}
        result = compile_payout_dispute(p)
        self.assertEqual(result["source"]["payout_route"]["value"], "@bryce")

    def test_unknown_route_type_is_rejected(self) -> None:
        p = payload()
        p["payout_route"]["type"] = "BANK_WIRE_AUTOSEND"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_settlement_timestamp_must_be_canonical_whole_second_utc(self) -> None:
        bad = (
            "2026-09-13T10:00:00-04:00",
            "2026-09-13T14:00:00.000Z",
            "2026-09-13T14:00Z",
        )
        for stamp in bad:
            with self.subTest(stamp=stamp):
                p = payload()
                p["settlement"]["settled_at"] = stamp
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_amounts_reject_bool_negative_nonfinite_and_huge_exponent(self) -> None:
        bad = (True, "-1", "NaN", "Infinity", "1e999")
        for value in bad:
            with self.subTest(value=value):
                p = payload()
                p["settlement"]["amount_received"] = value
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_positive_authority_amounts_reject_zero(self) -> None:
        for section, key in (
            ("work", "advertised_amount"),
            ("acceptance", "accepted_amount"),
        ):
            with self.subTest(section=section):
                p = payload()
                p[section][key] = "0"
                with self.assertRaises(PayoutDisputeInputError):
                    compile_payout_dispute(p)

    def test_decimal_normalization_is_exact_not_float_based(self) -> None:
        p = payload()
        p["acceptance"]["accepted_amount"] = "0.300"
        p["settlement"]["amount_received"] = "0.100"
        result = compile_payout_dispute(p)
        self.assertEqual(result["amounts"]["accepted_amount"], "0.3")
        self.assertEqual(result["amounts"]["received_amount"], "0.1")
        self.assertEqual(result["amounts"]["difference_amount"], "0.2")

    def test_control_characters_in_transaction_id_are_rejected(self) -> None:
        p = payload()
        p["settlement"]["transaction_id"] = "txn\nspoof"
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_unknown_fields_fail_closed(self) -> None:
        p = payload()
        p["settlement"]["sponsor_says_paid"] = True
        with self.assertRaises(PayoutDisputeInputError):
            compile_payout_dispute(p)

    def test_authority_ceiling_is_explicit(self) -> None:
        result = compile_payout_dispute(payload())
        authority = result["authority"]
        self.assertFalse(authority["external_send"])
        self.assertFalse(authority["provider_mutation"])
        self.assertFalse(authority["payment_mutation"])
        self.assertFalse(authority["wallet_mutation"])
        self.assertFalse(authority["legal_debt_claim"])
        self.assertFalse(authority["advertised_reward_is_debt"])
        self.assertFalse(authority["advertised_reward_is_earned_revenue"])
        self.assertFalse(authority["merge_proves_acceptance"])
        self.assertFalse(authority["evidence_ref_or_digest_authenticates_source"])
        self.assertTrue(authority["owner_review_required_before_contact"])

    def test_receipt_is_deterministic_and_tamper_evident(self) -> None:
        p = payload()
        one = compile_payout_dispute(p)
        two = compile_payout_dispute(copy.deepcopy(p))
        self.assertEqual(one, two)
        self.assertTrue(verify_payout_dispute(p, one))
        tampered = copy.deepcopy(one)
        tampered["amounts"]["difference_amount"] = "999"
        self.assertFalse(verify_payout_dispute(p, tampered))

    def test_tampered_authority_fails_verification(self) -> None:
        p = payload()
        result = compile_payout_dispute(p)
        result["authority"]["external_send"] = True
        self.assertFalse(verify_payout_dispute(p, result))

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(PayoutDisputeInputError):
                load_json(path)

    def test_nonfinite_json_constant_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('{"amount":NaN}', encoding="utf-8")
            with self.assertRaises(PayoutDisputeInputError):
                load_json(path)

    def test_round_trip_packet_json_preserves_verification(self) -> None:
        p = payload()
        result = compile_payout_dispute(p)
        round_tripped = json.loads(json.dumps(result))
        self.assertTrue(verify_payout_dispute(p, round_tripped))


if __name__ == "__main__":
    unittest.main()
