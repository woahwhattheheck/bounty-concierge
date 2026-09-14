import copy
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import concierge.collection_request as collection_request
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

    def test_valid_regular_file_control(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text('{"a":1}', encoding="utf-8")
            self.assertEqual(load_json(p), {"a": 1})

    def test_invalid_utf8_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_bytes(b'{"a":"\xff"}')
            with self.assertRaisesRegex(CollectionRequestInputError, "UTF-8"):
                load_json(p)

    def test_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(CollectionRequestInputError, "regular file"):
                load_json(Path(td))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "target.json"
            link = Path(td) / "link.json"
            target.write_text('{"a":1}', encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            with self.assertRaisesRegex(CollectionRequestInputError, "regular file"):
                load_json(link)

    @unittest.skipUnless(
        hasattr(os, "mkfifo") and hasattr(signal, "SIGALRM"),
        "FIFO/alarm unavailable",
    )
    def test_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = Path(td) / "input.fifo"
            os.mkfifo(fifo)

            def timed_out(_signum, _frame):
                raise TimeoutError("FIFO read blocked")

            previous = signal.signal(signal.SIGALRM, timed_out)
            signal.alarm(2)
            try:
                with self.assertRaisesRegex(CollectionRequestInputError, "regular file"):
                    load_json(fifo)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)

    @unittest.skipUnless(Path("/dev/null").exists(), "device control unavailable")
    def test_character_device_fails_closed(self):
        with self.assertRaisesRegex(CollectionRequestInputError, "regular file"):
            load_json(Path("/dev/null"))

    def test_oversized_sparse_file_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "oversized.json"
            with p.open("wb") as handle:
                handle.truncate(collection_request._MAX_JSON_BYTES + 1)
            with mock.patch.object(
                collection_request.os,
                "read",
                side_effect=AssertionError("oversized file must not be read"),
            ):
                with self.assertRaisesRegex(CollectionRequestInputError, "too large"):
                    load_json(p)

    def test_growth_during_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "growing.json"
            p.write_text('{"a":1}', encoding="utf-8")
            real_read = collection_request.os.read
            mutated = False

            def read_then_grow(fd, amount):
                nonlocal mutated
                chunk = real_read(fd, amount)
                if not mutated:
                    mutated = True
                    with p.open("ab") as handle:
                        handle.write(b" ")
                return chunk

            with mock.patch.object(collection_request.os, "read", side_effect=read_then_grow):
                with self.assertRaisesRegex(CollectionRequestInputError, "changed while reading"):
                    load_json(p)

    @unittest.skipUnless(hasattr(os, "link"), "hard links unavailable")
    def test_same_inode_same_size_alias_mutation_after_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "input.json"
            alias = Path(td) / "alias.json"
            p.write_text('{"a":1}', encoding="utf-8")
            try:
                os.link(p, alias)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            real_lstat = collection_request._lstat
            calls = 0

            def mutate_before_final_path_snapshot(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    alias.write_text('{"b":2}', encoding="utf-8")
                return real_lstat(path)

            with mock.patch.object(
                collection_request,
                "_lstat",
                side_effect=mutate_before_final_path_snapshot,
            ):
                with self.assertRaisesRegex(CollectionRequestInputError, "path changed while reading"):
                    load_json(p)
            self.assertEqual(p.read_text(encoding="utf-8"), '{"b":2}')

    def test_foreign_inode_path_replacement_after_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "input.json"
            replacement = Path(td) / "replacement.json"
            p.write_text('{"a":1}', encoding="utf-8")
            replacement.write_text('{"b":2}', encoding="utf-8")
            real_lstat = collection_request._lstat
            calls = 0

            def replace_before_final_path_snapshot(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    os.replace(replacement, p)
                return real_lstat(path)

            with mock.patch.object(
                collection_request,
                "_lstat",
                side_effect=replace_before_final_path_snapshot,
            ):
                with self.assertRaisesRegex(CollectionRequestInputError, "path changed while reading"):
                    load_json(p)
            self.assertEqual(p.read_text(encoding="utf-8"), '{"b":2}')

    def test_mutation_after_final_path_snapshot_is_caught_by_final_fstat(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "input.json"
            p.write_text('{"a":1}', encoding="utf-8")
            real_lstat = collection_request._lstat
            calls = 0

            def return_snapshot_then_mutate(path):
                nonlocal calls
                calls += 1
                snapshot = real_lstat(path)
                if calls == 2:
                    p.write_text('{"b":2}', encoding="utf-8")
                return snapshot

            with mock.patch.object(
                collection_request,
                "_lstat",
                side_effect=return_snapshot_then_mutate,
            ):
                with self.assertRaisesRegex(
                    CollectionRequestInputError,
                    "changed during final path validation",
                ):
                    load_json(p)
            self.assertEqual(p.read_text(encoding="utf-8"), '{"b":2}')

    def test_path_disappearance_before_final_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "input.json"
            p.write_text('{"a":1}', encoding="utf-8")
            real_lstat = collection_request._lstat
            calls = 0

            def remove_before_final_path_snapshot(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    p.unlink()
                return real_lstat(path)

            with mock.patch.object(
                collection_request,
                "_lstat",
                side_effect=remove_before_final_path_snapshot,
            ):
                with self.assertRaisesRegex(
                    CollectionRequestInputError,
                    "could not be inspected safely",
                ):
                    load_json(p)


if __name__ == "__main__":
    unittest.main()
