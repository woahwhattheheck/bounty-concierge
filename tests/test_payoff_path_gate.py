from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from concierge.payoff_path_gate import (
    PACKET_SCHEMA,
    PayoffPathError,
    compile_gate,
    compile_legacy_migration_gate,
    load_strict_json,
    main,
    verify_gate,
    verify_legacy_migration_gate,
)

AS_OF = "2026-09-13T15:00:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def path_for(mechanism="BOUNTY", kind="FIXED", amount=9000, currency="USD"):
    event = {
        "BOUNTY": "SUBMIT_WORK",
        "COMPETITION_PRIZE": "ENTER_COMPETITION",
        "PAID_OFFER_OR_PILOT": "SEND_PAID_OFFER",
        "PRIME_SUBCONTRACT": "SECURE_TEAMING",
        "REFERRAL_COMMISSION": "COMPLETE_REFERRAL",
        "SPONSOR_OR_GRANT": "APPLY_FOR_GRANT",
    }[mechanism]
    if kind in {"NEGOTIATED", "UNSPECIFIED_BY_SOURCE"}:
        amount = None
        currency = None
    return {
        "mechanism": mechanism,
        "value": {"kind": kind, "currency": currency, "amount_minor": amount},
        "source": {
            "canonical_url": "https://example.com/opportunity/42",
            "evidence_ref": "source:terms-v3",
            "evidence_sha256": SHA_A,
            "observed_at_utc": "2026-09-13T14:00:00.000Z",
            "max_age_days": 7,
        },
        "conversion": {
            "event": event,
            "due_at_utc": "2026-09-20T12:00:00.000Z",
            "evidence_ref": "conversion:deadline-v1",
            "evidence_sha256": SHA_B,
        },
    }


def item(work_id="work-1", path=None, budget=300, spent=45):
    return {
        "work_id": work_id,
        "opportunity_id": "opp-42",
        "started_at_utc": "2026-09-13T13:00:00.000Z",
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": path_for() if path is None else path,
    }


def doc(*rows):
    return {"schema": "payoff-path-work/v1", "work_items": list(rows or [item()])}


class PayoffPathGateTests(unittest.TestCase):
    def compile(self, document):
        # v1 is retained only as an explicit migration/history fixture. Normal
        # compile_gate(v1, ...) is production fail-closed and is covered by v3 tests.
        return compile_legacy_migration_gate(document, AS_OF)

    def verify(self, document, packet, markdown, receipt, trusted_now=AS_OF):
        return verify_legacy_migration_gate(
            document, packet, markdown, receipt, trusted_now
        )

    def test_valid_bounty_is_ready_without_claiming_payment(self):
        packet, markdown, receipt = self.compile(doc())
        self.assertEqual(PACKET_SCHEMA, packet["schema"])
        row = packet["results"][0]
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", row["state"])
        self.assertEqual(255, row["free_work_remaining_minutes"])
        self.assertNotIn("won", markdown.lower())
        self.assertNotIn("payment received", markdown.lower())
        self.assertNotIn("award confirmed", markdown.lower())
        self.assertTrue(self.verify(doc(), packet, markdown, receipt))

    def test_all_supported_payoff_mechanisms_are_ready(self):
        mechanisms = [
            ("BOUNTY", "FIXED"),
            ("COMPETITION_PRIZE", "POOL"),
            ("PAID_OFFER_OR_PILOT", "NEGOTIATED"),
            ("PRIME_SUBCONTRACT", "NEGOTIATED"),
            ("REFERRAL_COMMISSION", "UNSPECIFIED_BY_SOURCE"),
            ("SPONSOR_OR_GRANT", "POOL"),
        ]
        rows = [item(f"w-{i}", path_for(mechanism, kind)) for i, (mechanism, kind) in enumerate(mechanisms)]
        packet, _, _ = self.compile(doc(*rows))
        self.assertEqual(6, packet["summary"]["READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW"])

    def test_no_payoff_path_holds(self):
        row = item()
        row["payoff_path"] = None
        packet, _, _ = self.compile(doc(row))
        self.assertEqual("HOLD_NO_PAYOFF_PATH", packet["results"][0]["state"])

    def test_spent_exactly_at_cap_stops(self):
        packet, _, _ = self.compile(doc(item(budget=60, spent=60)))
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])
        self.assertEqual(0, packet["results"][0]["free_work_remaining_minutes"])

    def test_spent_over_cap_stops_even_without_path(self):
        row = item(budget=60, spent=61)
        row["payoff_path"] = None
        packet, _, _ = self.compile(doc(row))
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])

    def test_stale_source_holds(self):
        p = path_for()
        p["source"]["observed_at_utc"] = "2026-08-01T00:00:00.000Z"
        p["source"]["max_age_days"] = 7
        packet, _, _ = self.compile(doc(item(path=p)))
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("SOURCE_EVIDENCE_STALE", packet["results"][0]["reasons"])

    def test_future_source_holds(self):
        p = path_for()
        p["source"]["observed_at_utc"] = "2026-09-13T16:00:00.000Z"
        packet, _, _ = self.compile(doc(item(path=p)))
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("SOURCE_EVIDENCE_FROM_FUTURE", packet["results"][0]["reasons"])

    def test_expired_conversion_holds(self):
        p = path_for()
        p["conversion"]["due_at_utc"] = "2026-09-13T14:59:59.000Z"
        packet, _, _ = self.compile(doc(item(path=p)))
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("CONVERSION_DEADLINE_EXPIRED", packet["results"][0]["reasons"])

    def test_conversion_must_follow_source_observation(self):
        p = path_for()
        p["conversion"]["due_at_utc"] = "2026-09-13T13:00:00.000Z"
        packet, _, _ = self.compile(doc(item(path=p)))
        self.assertIn("CONVERSION_DEADLINE_NOT_AFTER_SOURCE_OBSERVATION", packet["results"][0]["reasons"])

    def test_work_start_future_holds(self):
        row = item()
        row["started_at_utc"] = "2026-09-13T16:00:00.000Z"
        packet, _, _ = self.compile(doc(row))
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("WORK_STARTS_IN_FUTURE", packet["results"][0]["reasons"])

    def test_wrong_event_for_mechanism_rejected(self):
        p = path_for("BOUNTY")
        p["conversion"]["event"] = "ENTER_COMPETITION"
        with self.assertRaisesRegex(PayoffPathError, "does not match"):
            self.compile(doc(item(path=p)))

    def test_unsupported_mechanism_rejected(self):
        p = path_for()
        p["mechanism"] = "FREE_HELP_MAYBE_LATER"
        with self.assertRaisesRegex(PayoffPathError, "unsupported"):
            self.compile(doc(item(path=p)))

    def test_fixed_and_pool_require_positive_exact_amount(self):
        for bad in [0, -1, True, 9_007_199_254_740_992]:
            p = path_for()
            p["value"]["amount_minor"] = bad
            with self.subTest(bad=bad), self.assertRaises(PayoffPathError):
                self.compile(doc(item(path=p)))

    def test_fixed_requires_currency(self):
        p = path_for()
        p["value"]["currency"] = "usd"
        with self.assertRaises(PayoffPathError):
            self.compile(doc(item(path=p)))

    def test_negotiated_cannot_invent_amount(self):
        p = path_for("PRIME_SUBCONTRACT", "NEGOTIATED")
        p["value"]["currency"] = "USD"
        p["value"]["amount_minor"] = 500000
        with self.assertRaisesRegex(PayoffPathError, "invented numeric value"):
            self.compile(doc(item(path=p)))

    def test_unspecified_cannot_invent_currency_only(self):
        p = path_for("REFERRAL_COMMISSION", "UNSPECIFIED_BY_SOURCE")
        p["value"]["currency"] = "USD"
        with self.assertRaises(PayoffPathError):
            self.compile(doc(item(path=p)))

    def test_bool_budget_and_spent_rejected(self):
        row = item()
        row["free_work_budget_minutes"] = True
        with self.assertRaises(PayoffPathError):
            self.compile(doc(row))
        row = item()
        row["free_work_spent_minutes"] = False
        with self.assertRaises(PayoffPathError):
            self.compile(doc(row))

    def test_zero_budget_rejected(self):
        with self.assertRaises(PayoffPathError):
            self.compile(doc(item(budget=0)))

    def test_bad_sha_url_and_ref_rejected(self):
        mutations = []
        p = path_for(); p["source"]["evidence_sha256"] = "A" * 64; mutations.append(p)
        p = path_for(); p["source"]["canonical_url"] = "http://example.com/opportunity"; mutations.append(p)
        p = path_for(); p["source"]["canonical_url"] = "https://u:p@example.com/opportunity"; mutations.append(p)
        p = path_for(); p["source"]["evidence_ref"] = "buyer-email"; mutations.append(p)
        for p in mutations:
            with self.subTest(p=p), self.assertRaises(PayoffPathError):
                self.compile(doc(item(path=p)))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(PayoffPathError, "duplicate JSON key"):
            load_strict_json('{"schema":"payoff-path-work/v1","schema":"x","work_items":[]}')

    def test_unknown_fields_rejected(self):
        document = doc()
        document["work_items"][0]["hope"] = "maybe"
        with self.assertRaisesRegex(PayoffPathError, "keys mismatch"):
            self.compile(document)

    def test_duplicate_work_ids_rejected(self):
        with self.assertRaisesRegex(PayoffPathError, "duplicate work_id"):
            self.compile(doc(item("same"), item("same")))

    def test_input_order_is_deterministic(self):
        a = item("a")
        b = item("b", path_for("COMPETITION_PRIZE", "POOL"))
        first = self.compile(doc(a, b))
        second = self.compile(doc(b, a))
        self.assertEqual(first, second)

    def test_tampered_packet_markdown_receipt_rejected(self):
        document = doc()
        packet, markdown, receipt = self.compile(document)
        bad_packet = deepcopy(packet)
        bad_packet["results"][0]["free_work_remaining_minutes"] += 1
        with self.assertRaises(PayoffPathError):
            self.verify(document, bad_packet, markdown, receipt)
        with self.assertRaises(PayoffPathError):
            self.verify(document, packet, markdown + "tamper", receipt)
        bad_receipt = deepcopy(receipt)
        bad_receipt["markdown_sha256"] = "0" * 64
        with self.assertRaises(PayoffPathError):
            self.verify(document, packet, markdown, bad_receipt)

    def test_changed_source_or_cap_invalidates_packet(self):
        document = doc()
        packet, markdown, receipt = self.compile(document)
        changed = deepcopy(document)
        changed["work_items"][0]["free_work_budget_minutes"] += 1
        with self.assertRaises(PayoffPathError):
            self.verify(changed, packet, markdown, receipt)
        changed = deepcopy(document)
        changed["work_items"][0]["payoff_path"]["source"]["evidence_sha256"] = "c" * 64
        with self.assertRaises(PayoffPathError):
            self.verify(changed, packet, markdown, receipt)

    def test_previous_ready_fails_verification_after_deadline(self):
        document = doc()
        packet, markdown, receipt = self.compile(document)
        with self.assertRaisesRegex(PayoffPathError, "no longer current"):
            self.verify(document, packet, markdown, receipt, "2026-09-21T00:00:00.000Z")

    def test_packet_from_future_rejected(self):
        document = doc()
        packet, markdown, receipt = self.compile(document)
        with self.assertRaisesRegex(PayoffPathError, "future"):
            self.verify(document, packet, markdown, receipt, "2026-09-13T14:00:00.000Z")

    def test_cli_has_no_as_of_override(self):
        # The production parser intentionally rejects arbitrary --as-of authority.
        with self.assertRaises(SystemExit):
            main([
                "compile", "--input", "x", "--packet", "p", "--markdown", "m",
                "--receipt", "r", "--as-of", AS_OF,
            ])

    def test_cli_compile_and_verify_create_exclusive_bundle(self):
        document = doc()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            input_path.write_text(json.dumps(document), encoding="utf-8")
            packet = root / "packet.json"
            markdown = root / "review.md"
            receipt = root / "receipt.json"
            self.assertEqual(
                0,
                main([
                    "compile", "--input", str(input_path), "--packet", str(packet),
                    "--markdown", str(markdown), "--receipt", str(receipt),
                ]),
            )
            self.assertTrue(packet.exists() and markdown.exists() and receipt.exists())
            self.assertEqual(
                0,
                main([
                    "verify", "--input", str(input_path), "--packet", str(packet),
                    "--markdown", str(markdown), "--receipt", str(receipt),
                ]),
            )
            with self.assertRaises(SystemExit):
                main([
                    "compile", "--input", str(input_path), "--packet", str(packet),
                    "--markdown", str(markdown), "--receipt", str(receipt),
                ])

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink unavailable")
    def test_cli_refuses_final_symlink_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            input_path.write_text(json.dumps(doc()), encoding="utf-8")
            target = root / "target.json"
            target.write_text("do-not-touch", encoding="utf-8")
            packet = root / "packet.json"
            os.symlink(target, packet)
            with self.assertRaises(SystemExit):
                main([
                    "compile", "--input", str(input_path), "--packet", str(packet),
                    "--markdown", str(root / "review.md"), "--receipt", str(root / "receipt.json"),
                ])
            self.assertEqual("do-not-touch", target.read_text(encoding="utf-8"))

    @unittest.skipIf(not hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_cli_refuses_non_regular_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "input.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(SystemExit):
                main([
                    "compile", "--input", str(fifo), "--packet", str(root / "p"),
                    "--markdown", str(root / "m"), "--receipt", str(root / "r"),
                ])

    def test_cli_refuses_symlink_input(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            actual.write_text(json.dumps(doc()), encoding="utf-8")
            link = root / "input.json"
            os.symlink(actual, link)
            with self.assertRaises(SystemExit):
                main([
                    "compile", "--input", str(link), "--packet", str(root / "p"),
                    "--markdown", str(root / "m"), "--receipt", str(root / "r"),
                ])

    def test_empty_document_is_valid_and_deterministic(self):
        legacy = {"schema": "payoff-path-work/v1", "work_items": []}
        packet, markdown, receipt = self.compile(legacy)
        self.assertEqual(0, packet["summary"]["total_items"])
        self.assertIn("No speculative work items", markdown)
        self.assertTrue(self.verify(legacy, packet, markdown, receipt))


if __name__ == "__main__":
    unittest.main()
