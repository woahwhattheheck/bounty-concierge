import copy
import json
import tempfile
import unittest
from pathlib import Path

from concierge.collection_request import (
    CollectionRequestInputError,
    compile_collection_request,
    load_json,
    verify_collection_request,
)


class CollectionRequestTests(unittest.TestCase):
    def base(self):
        return {
            "schema": "bounty-collection-request-input/v1",
            "sponsor_name": "Sponsor Team",
            "work": {
                "repo": "acme/widget",
                "pr": 42,
                "canonical_url": "https://github.com/acme/widget/pull/42",
                "head_sha": "a" * 40,
                "state": "MERGED",
                "advertised_amount": "90.00",
                "currency": "USD",
            },
            "payout_route": {"type": "PAYMENT_LINK", "value": "https://pay.example/abc"},
            "acceptance": {"kind": "NONE", "evidence_ref": None, "evidence_sha256": None},
        }

    def test_merge_only_is_direct_assessment_request(self):
        packet = compile_collection_request(self.base())
        self.assertEqual(packet["disposition"], "READY_TO_REQUEST_ASSESSMENT")
        self.assertIn("requesting assessment", packet["body"])
        self.assertIn("Advertised reward: 90 USD", packet["body"])
        self.assertIn("Please confirm acceptance and, if accepted, arrange", packet["body"])
        self.assertNotIn("eligible", packet["body"].lower())
        self.assertFalse(packet["authority"]["merge_proves_acceptance"])
        self.assertFalse(packet["authority"]["advertised_reward_is_debt"])

    def test_separate_acceptance_unlocks_payment_request(self):
        value = self.base()
        value["acceptance"] = {
            "kind": "SPONSOR_ACCEPTED",
            "evidence_ref": "https://github.com/acme/widget/pull/42#issuecomment-99",
            "evidence_sha256": "b" * 64,
        }
        packet = compile_collection_request(value)
        self.assertEqual(packet["disposition"], "READY_TO_REQUEST_PAYMENT")
        self.assertIn("requesting the advertised 90 USD payout", packet["body"])
        self.assertIn("confirm when it has been initiated", packet["body"])
        self.assertFalse(packet["authority"]["payment_due_claim"])

    def test_awarded_also_unlocks_payment_wording(self):
        value = self.base()
        value["acceptance"] = {
            "kind": "AWARDED",
            "evidence_ref": "https://sponsor.example/award/42",
            "evidence_sha256": "c" * 64,
        }
        self.assertEqual(compile_collection_request(value)["disposition"], "READY_TO_REQUEST_PAYMENT")

    def test_none_cannot_smuggle_acceptance_evidence(self):
        value = self.base()
        value["acceptance"]["evidence_ref"] = "https://sponsor.example/x"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_acceptance_requires_digest(self):
        value = self.base()
        value["acceptance"] = {
            "kind": "SPONSOR_ACCEPTED",
            "evidence_ref": "https://sponsor.example/x",
            "evidence_sha256": None,
        }
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_canonical_pr_url_is_bound(self):
        value = self.base()
        value["work"]["canonical_url"] = "https://github.com/acme/widget/pull/43"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_nonmerged_work_cannot_generate_collection_request(self):
        value = self.base()
        value["work"]["state"] = "OPEN"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_amount_normalized_exactly(self):
        value = self.base()
        value["work"]["advertised_amount"] = "090.5000"
        packet = compile_collection_request(value)
        self.assertEqual(packet["source"]["work"]["advertised_amount"], "90.5")
        self.assertIn("90.5 USD", packet["body"])

    def test_nonfinite_amount_rejected(self):
        value = self.base()
        value["work"]["advertised_amount"] = "NaN"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_zero_and_negative_amount_rejected(self):
        for amount in ("0", "-1"):
            value = self.base()
            value["work"]["advertised_amount"] = amount
            with self.assertRaises(CollectionRequestInputError):
                compile_collection_request(value)

    def test_currency_is_strict(self):
        value = self.base()
        value["work"]["currency"] = "usd"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_payment_link_must_be_https(self):
        value = self.base()
        value["payout_route"]["value"] = "http://pay.example/abc"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_receipt_is_deterministic(self):
        a = compile_collection_request(self.base())
        b = compile_collection_request(copy.deepcopy(self.base()))
        self.assertEqual(a, b)
        self.assertEqual(len(a["receipt_sha256"]), 64)

    def test_verify_rejects_body_tamper(self):
        payload = self.base()
        packet = compile_collection_request(payload)
        packet["body"] += " Payment is legally due."
        self.assertFalse(verify_collection_request(payload, packet))

    def test_verify_rejects_authority_escalation_even_with_rehashed_shape(self):
        payload = self.base()
        packet = compile_collection_request(payload)
        packet["authority"]["payment_due_claim"] = True
        self.assertFalse(verify_collection_request(payload, packet))

    def test_verify_rejects_source_mutation(self):
        payload = self.base()
        packet = compile_collection_request(payload)
        changed = copy.deepcopy(payload)
        changed["work"]["advertised_amount"] = "100"
        self.assertFalse(verify_collection_request(changed, packet))

    def test_unknown_fields_fail_closed(self):
        value = self.base()
        value["work"]["bonus"] = "999"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_bool_pr_rejected(self):
        value = self.base()
        value["work"]["pr"] = True
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_control_characters_rejected(self):
        value = self.base()
        value["sponsor_name"] = "Sponsor\nBcc: attacker@example"
        with self.assertRaises(CollectionRequestInputError):
            compile_collection_request(value)

    def test_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text('{"a":1,"a":2}', encoding="utf-8")
            with self.assertRaises(CollectionRequestInputError):
                load_json(p)

    def test_nonfinite_json_constant_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text('{"x":NaN}', encoding="utf-8")
            with self.assertRaises(CollectionRequestInputError):
                load_json(p)

    def test_roundtrip_json_packet(self):
        payload = self.base()
        packet = compile_collection_request(payload)
        encoded = json.dumps(packet, sort_keys=True)
        self.assertTrue(verify_collection_request(payload, json.loads(encoded)))


if __name__ == "__main__":
    unittest.main()
