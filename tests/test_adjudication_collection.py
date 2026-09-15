import copy
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from concierge import adjudication_collection as ac
from concierge.sponsor_adjudication import AdjudicationError, compile_manifest
from concierge.sponsor_adjudication import authority as sa_authority
from concierge.sponsor_adjudication.common import _sha256_obj


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


TEST_KEY = "11" * 32
TEST_PROVIDER = "gmail"
TEST_PRINCIPAL = h("test-principal")


def base_manifest():
    return {
        "schema_version": 1,
        "program": {
            "program_id": "program-1",
            "sponsor": "Synthetic Sponsor",
            "source_ref": "https://example.invalid/program",
        },
        "findings": [
            {
                "finding_id": "f-1",
                "fingerprint_sha256": h("f1"),
                "title": "Parser escape",
                "submitted_by": "tester",
                "evidence_ref": "repo#1",
            },
            {
                "finding_id": "f-2",
                "fingerprint_sha256": h("f2"),
                "title": "Quota poisoning",
                "submitted_by": "tester",
                "evidence_ref": "repo#2",
            },
        ],
        "submissions": [
            {
                "submission_id": "s-1",
                "finding_ids": ["f-1"],
                "submitted_at": "2026-09-13T20:00:00Z",
                "channel": "email",
                "receipt_ref": "mail:m1",
                "receipt_sha256": h("m1"),
            },
            {
                "submission_id": "s-2",
                "finding_ids": ["f-2"],
                "submitted_at": "2026-09-13T20:01:00Z",
                "channel": "email",
                "receipt_ref": "mail:m2",
                "receipt_sha256": h("m2"),
            },
        ],
        "sponsor_events": [
            {
                "event_id": "e-1",
                "event_type": "SPONSOR_VERIFIED",
                "event_at": "2026-09-13T21:00:00Z",
                "source_ref": "https://sponsor.example/evidence/verified",
                "source_sha256": h("verified"),
                "claim_unit_id": "unit-1",
                "finding_ids": ["f-1", "f-2"],
                "submission_ids": ["s-1", "s-2"],
            },
            {
                "event_id": "e-2",
                "event_type": "ADJUDICATION_STARTED",
                "event_at": "2026-09-13T21:01:00Z",
                "source_ref": "https://sponsor.example/evidence/adjudicating",
                "source_sha256": h("adjudicating"),
                "claim_unit_id": "unit-1",
            },
        ],
    }


def add_offer(
    m,
    *,
    event_id="e-3",
    at="2026-09-13T21:02:00Z",
    source="https://sponsor.example/evidence/offer",
    amount="90.00",
    currency="USD",
):
    m["sponsor_events"].append(
        {
            "event_id": event_id,
            "event_type": "REWARD_OFFERED",
            "event_at": at,
            "source_ref": source,
            "source_sha256": h(event_id + source),
            "claim_unit_id": "unit-1",
            "amount": amount,
            "currency": currency,
        }
    )
    return m


def signed_report(manifest):
    m = copy.deepcopy(manifest)
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    m["sponsor_authority"] = sa_authority.sign_for_test_or_host_fixture(
        m,
        m["program"],
        m["sponsor_events"],
        captured_at=captured_at,
    )
    return compile_manifest(m)


def rehash_report(report):
    core = {key: copy.deepcopy(value) for key, value in report.items() if key != "report_sha256"}
    report["report_sha256"] = _sha256_obj(core)
    return report


def work():
    return {
        "repo": "acme/widget",
        "pr": 42,
        "canonical_url": "https://github.com/acme/widget/pull/42",
        "head_sha": "a" * 40,
        "state": "MERGED",
    }


def route(value="https://pay.example/bryce"):
    return {"type": "PAYMENT_LINK", "value": value}


def payload_from_report(report, *, claim_unit_id="unit-1", payout_route=None, work_record=None):
    return {
        "schema": ac.INPUT_SCHEMA,
        "adjudication_report": report,
        "claim_unit_id": claim_unit_id,
        "work": work() if work_record is None else work_record,
        "payout_route": payout_route,
    }


def payload(manifest, *, claim_unit_id="unit-1", payout_route=None, work_record=None):
    return payload_from_report(
        signed_report(manifest),
        claim_unit_id=claim_unit_id,
        payout_route=payout_route,
        work_record=work_record,
    )


class AdjudicationCollectionTests(unittest.TestCase):
    def setUp(self):
        self.host_env = patch.dict(
            os.environ,
            {
                sa_authority.KEY_ENV: TEST_KEY,
                sa_authority.PROVIDER_ENV: TEST_PROVIDER,
                sa_authority.PRINCIPAL_ENV: TEST_PRINCIPAL,
                sa_authority.TEST_UNSIGNED_ENV: "0",
            },
            clear=False,
        )
        self.host_env.start()
        self.addCleanup(self.host_env.stop)

    def test_reward_offer_compiles_one_host_authorized_claim_unit_payment_draft(self):
        packet = ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=route()))
        self.assertEqual(packet["disposition"], "OWNER_REVIEW_REQUIRED")
        self.assertEqual(packet["selected_claim_unit_id"], "unit-1")
        self.assertEqual(packet["canonical_claim_unit_id"], "unit-1")
        self.assertEqual(packet["finding_ids"], ["f-1", "f-2"])
        self.assertEqual(packet["submission_ids"], ["s-1", "s-2"])
        self.assertEqual(packet["reward_offer_evidence"]["amount"], "90.00")
        self.assertEqual(packet["reward_offer_evidence"]["currency"], "USD")
        self.assertEqual(packet["sponsor_authority"]["provider"], TEST_PROVIDER)
        self.assertEqual(packet["sponsor_authority"]["principal_sha256"], TEST_PRINCIPAL)
        self.assertRegex(packet["sponsor_authority"]["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(packet["sponsor_authority"]["report_projection_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsNotNone(packet["collection_key"])
        self.assertIn("sponsor-offered 90.00 USD", packet["draft"]["subject"])
        self.assertIn("OWNER REVIEW", packet["draft"]["subject"])
        self.assertIn("Candidate work reference (caller-supplied; unverified)", packet["draft"]["body"])
        self.assertNotIn("Merged work:", packet["draft"]["body"])
        self.assertNotIn("Exact merged head:", packet["draft"]["body"])
        self.assertIn("Sponsor claim unit: unit-1", packet["draft"]["body"])
        self.assertNotIn("advertised", packet["draft"]["body"].lower())
        self.assertNotIn("owed", packet["draft"]["body"].lower())

    def test_many_findings_still_emit_one_collection_generation(self):
        packet = ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=route()))
        self.assertEqual(len(packet["finding_ids"]), 2)
        self.assertIsInstance(packet["collection_key"], str)
        self.assertIsInstance(packet["draft"], dict)

    def test_later_identical_offer_reaffirmation_does_not_mint_new_key(self):
        first = add_offer(base_manifest())
        p1 = ac.compile_adjudication_collection(payload(copy.deepcopy(first), payout_route=route()))
        second = copy.deepcopy(first)
        add_offer(
            second,
            event_id="e-4",
            at="2026-09-13T21:03:00Z",
            source="https://sponsor.example/evidence/offer-reaffirmed",
        )
        p2 = ac.compile_adjudication_collection(payload(second, payout_route=route()))
        self.assertEqual(p1["collection_key"], p2["collection_key"])
        self.assertNotEqual(p1["adjudication_report_sha256"], p2["adjudication_report_sha256"])
        self.assertEqual(p2["reward_offer_evidence"]["event_id"], "e-3")

    def test_route_change_does_not_mint_new_key_but_changes_receipt(self):
        report = signed_report(add_offer(base_manifest()))
        p1 = ac.compile_adjudication_collection(payload_from_report(copy.deepcopy(report), payout_route=route("https://pay.example/a")))
        p2 = ac.compile_adjudication_collection(payload_from_report(copy.deepcopy(report), payout_route=route("https://pay.example/b")))
        self.assertEqual(p1["collection_key"], p2["collection_key"])
        self.assertNotEqual(p1["payout_route_sha256"], p2["payout_route_sha256"])
        self.assertNotEqual(p1["receipt_sha256"], p2["receipt_sha256"])

    def test_work_identity_change_does_not_mint_second_commercial_generation(self):
        report = signed_report(add_offer(base_manifest()))
        p1 = ac.compile_adjudication_collection(payload_from_report(copy.deepcopy(report), payout_route=route()))
        w2 = work()
        w2["head_sha"] = "b" * 40
        p2 = ac.compile_adjudication_collection(payload_from_report(copy.deepcopy(report), payout_route=route(), work_record=w2))
        self.assertEqual(p1["collection_key"], p2["collection_key"])
        self.assertNotEqual(p1["receipt_sha256"], p2["receipt_sha256"])

    def test_reward_evidence_permalink_change_does_not_mint_second_generation(self):
        m1 = add_offer(base_manifest(), source="https://sponsor.example/evidence/offer-a")
        m2 = add_offer(base_manifest(), source="https://sponsor.example/evidence/offer-b")
        p1 = ac.compile_adjudication_collection(payload(m1, payout_route=route()))
        p2 = ac.compile_adjudication_collection(payload(m2, payout_route=route()))
        self.assertEqual(p1["collection_key"], p2["collection_key"])
        self.assertNotEqual(p1["reward_offer_evidence"]["source_ref"], p2["reward_offer_evidence"]["source_ref"])
        self.assertNotEqual(p1["receipt_sha256"], p2["receipt_sha256"])

    def test_unicode_format_control_is_rejected_before_render(self):
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "unsafe control/format"):
            ac.compile_adjudication_collection(
                payload(
                    add_offer(base_manifest()),
                    payout_route={"type": "HOSTED_HANDLE", "value": "acct\u202eready"},
                )
            )

    def test_repo_case_alias_normalizes_to_same_collection_generation(self):
        report = signed_report(add_offer(base_manifest()))
        p1 = ac.compile_adjudication_collection(
            payload_from_report(copy.deepcopy(report), payout_route=route(), work_record=work())
        )
        alias = work()
        alias["repo"] = "AcMe/WiDgEt"
        alias["canonical_url"] = "https://github.com/AcMe/WiDgEt/pull/42"
        p2 = ac.compile_adjudication_collection(
            payload_from_report(copy.deepcopy(report), payout_route=route(), work_record=alias)
        )
        self.assertEqual(p1["collection_key"], p2["collection_key"])
        self.assertEqual(p1["work"], p2["work"])
        self.assertEqual(p2["work"]["repo"], "acme/widget")
        self.assertEqual(p2["work"]["canonical_url"], "https://github.com/acme/widget/pull/42")


    def test_unrelated_fabricated_work_never_becomes_send_ready_or_factual(self):
        report = signed_report(add_offer(base_manifest()))
        fake = {
            "repo": "attacker/unrelated",
            "pr": 999,
            "canonical_url": "https://github.com/attacker/unrelated/pull/999",
            "head_sha": "f" * 40,
            "state": "MERGED",
        }
        packet = ac.compile_adjudication_collection(
            payload_from_report(report, payout_route=route(), work_record=fake)
        )
        self.assertEqual(packet["disposition"], "OWNER_REVIEW_REQUIRED")
        self.assertFalse(packet["authority"]["work_state_independently_verified"])
        self.assertFalse(packet["authority"]["work_claim_unit_relation_verified"])
        self.assertIn("caller-supplied; unverified", packet["draft"]["body"])
        self.assertNotIn("Merged work:", packet["draft"]["body"])
        self.assertNotIn("Exact merged head:", packet["draft"]["body"])
        self.assertIn("independently verify", packet["draft"]["body"])

    def test_bool_pr_is_rejected(self):
        w = work()
        w["pr"] = True
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "bool is invalid"):
            ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=route(), work_record=w))

    def test_nonmerged_work_is_rejected(self):
        w = work()
        w["state"] = "OPEN"
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "must be MERGED"):
            ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=route(), work_record=w))

    def test_non_https_reward_offer_holds_without_route(self):
        packet = ac.compile_adjudication_collection(payload(add_offer(base_manifest(), source="mail:offer"), payout_route=None))
        self.assertEqual(packet["disposition"], "HOLD_FOR_ACCEPTANCE_EVIDENCE_URL")
        self.assertIsNone(packet["draft"])
        self.assertIsNone(packet["collection_key"])
        self.assertIsNone(packet["payout_route_sha256"])

    def test_non_https_reward_offer_rejects_premature_route(self):
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "payout_route must be null"):
            ac.compile_adjudication_collection(payload(add_offer(base_manifest(), source="mail:offer"), payout_route=route()))

    def test_later_https_reaffirmation_can_unlock_non_https_original(self):
        m = add_offer(base_manifest(), source="mail:offer")
        add_offer(
            m,
            event_id="e-4",
            at="2026-09-13T21:03:00Z",
            source="https://sponsor.example/evidence/offer-permalink",
        )
        packet = ac.compile_adjudication_collection(payload(m, payout_route=route()))
        self.assertEqual(packet["disposition"], "OWNER_REVIEW_REQUIRED")
        self.assertEqual(packet["reward_offer_evidence"]["event_id"], "e-4")

    def test_reward_offer_requires_route_once_evidence_is_send_ready(self):
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "payout_route is required"):
            ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=None))

    def test_payment_report_holds_for_settlement_instead_of_resending(self):
        m = add_offer(base_manifest())
        m["sponsor_events"].append(
            {
                "event_id": "e-4",
                "event_type": "PAYMENT_REPORTED",
                "event_at": "2026-09-13T21:03:00Z",
                "source_ref": "https://sponsor.example/evidence/payment-reported",
                "source_sha256": h("payment-reported"),
                "claim_unit_id": "unit-1",
                "amount": "90.00",
                "currency": "USD",
            }
        )
        packet = ac.compile_adjudication_collection(payload(m, payout_route=None))
        self.assertEqual(packet["disposition"], "HOLD_FOR_SETTLEMENT_EVIDENCE")
        self.assertIsNone(packet["draft"])
        self.assertIsNone(packet["collection_key"])

    def test_verified_and_adjudicating_units_wait_without_route(self):
        packet = ac.compile_adjudication_collection(payload(base_manifest(), payout_route=None))
        self.assertEqual(packet["disposition"], "WAIT_SPONSOR")
        self.assertIsNone(packet["draft"])

    def test_waiting_unit_rejects_payout_route(self):
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "payout_route must be null"):
            ac.compile_adjudication_collection(payload(base_manifest(), payout_route=route()))

    def test_declined_unit_is_do_not_request(self):
        m = base_manifest()
        m["sponsor_events"].append(
            {
                "event_id": "e-3",
                "event_type": "DECLINED",
                "event_at": "2026-09-13T21:02:00Z",
                "source_ref": "https://sponsor.example/evidence/declined",
                "source_sha256": h("declined"),
                "claim_unit_id": "unit-1",
            }
        )
        packet = ac.compile_adjudication_collection(payload(m, payout_route=None))
        self.assertEqual(packet["disposition"], "DO_NOT_REQUEST")
        self.assertEqual(packet["canonical_claim_unit_id"], "unit-1")

    def test_collapsed_source_is_do_not_request_and_points_to_target(self):
        m = base_manifest()
        m["sponsor_events"] = [
            {
                "event_id": "a",
                "event_type": "SPONSOR_RECEIVED",
                "event_at": "2026-09-13T21:00:00Z",
                "source_ref": "https://sponsor.example/a",
                "source_sha256": h("a"),
                "claim_unit_id": "u-a",
                "finding_ids": ["f-1"],
                "submission_ids": ["s-1"],
            },
            {
                "event_id": "b",
                "event_type": "SPONSOR_RECEIVED",
                "event_at": "2026-09-13T21:00:01Z",
                "source_ref": "https://sponsor.example/b",
                "source_sha256": h("b"),
                "claim_unit_id": "u-b",
                "finding_ids": ["f-2"],
                "submission_ids": ["s-2"],
            },
            {
                "event_id": "c",
                "event_type": "DUPLICATE_COLLAPSED",
                "event_at": "2026-09-13T21:01:00Z",
                "source_ref": "https://sponsor.example/c",
                "source_sha256": h("c"),
                "claim_unit_id": "u-a",
                "collapsed_claim_unit_ids": ["u-b"],
            },
        ]
        packet = ac.compile_adjudication_collection(payload(m, claim_unit_id="u-b", payout_route=None))
        self.assertEqual(packet["disposition"], "DO_NOT_REQUEST")
        self.assertEqual(packet["canonical_claim_unit_id"], "u-a")
        self.assertIsNone(packet["collection_key"])

    def test_missing_claim_unit_is_rejected(self):
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "compiled sponsor claim unit"):
            ac.compile_adjudication_collection(payload(base_manifest(), claim_unit_id="nope", payout_route=None))

    def test_unsigned_test_mode_is_refused_even_with_a_signed_report(self):
        report = signed_report(add_offer(base_manifest()))
        with patch.dict(os.environ, {sa_authority.TEST_UNSIGNED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(ac.AdjudicationCollectionError, "refuses unsigned"):
                ac.compile_adjudication_collection(payload_from_report(report, payout_route=route()))

    def test_unsigned_retained_report_is_rejected_when_bridge_verifies_authority(self):
        m = add_offer(base_manifest())
        with patch.dict(os.environ, {sa_authority.TEST_UNSIGNED_ENV: "1"}, clear=False):
            unsigned_report = compile_manifest(copy.deepcopy(m))
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "invalid retained adjudication report"):
            ac.compile_adjudication_collection(payload_from_report(unsigned_report, payout_route=route()))

    def test_rehashed_derived_reward_drift_is_rejected_by_report_generation_hmac(self):
        report = signed_report(add_offer(base_manifest()))
        report["claim_units"][0]["reward_amount"] = "999"
        rehash_report(report)
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "invalid retained adjudication report"):
            ac.compile_adjudication_collection(payload_from_report(report, payout_route=route()))

    def test_rehashed_derived_membership_drift_is_rejected_by_report_generation_hmac(self):
        report = signed_report(add_offer(base_manifest()))
        report["claim_units"][0]["finding_ids"] = ["f-1"]
        rehash_report(report)
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "invalid retained adjudication report"):
            ac.compile_adjudication_collection(payload_from_report(report, payout_route=route()))

    def test_authenticated_event_tamper_is_rejected_even_if_report_digest_is_rehashed(self):
        report = signed_report(add_offer(base_manifest()))
        offer = next(e for e in report["sponsor_events"] if e["event_type"] == "REWARD_OFFERED")
        offer["amount"] = "999"
        report["claim_units"][0]["reward_amount"] = "999"
        rehash_report(report)
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "invalid retained adjudication report"):
            ac.compile_adjudication_collection(payload_from_report(report, payout_route=route()))

    def test_host_authority_tamper_is_rejected_even_if_report_digest_is_rehashed(self):
        report = signed_report(add_offer(base_manifest()))
        report["program"]["sponsor_authority"]["provider"] = "other-provider"
        rehash_report(report)
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "invalid retained adjudication report"):
            ac.compile_adjudication_collection(payload_from_report(report, payout_route=route()))

    def test_root_extra_key_is_rejected(self):
        p = payload(base_manifest(), payout_route=None)
        p["surprise"] = True
        with self.assertRaisesRegex(ac.AdjudicationCollectionError, "exactly"):
            ac.compile_adjudication_collection(p)

    def test_verify_round_trip_and_tamper(self):
        p = payload(add_offer(base_manifest()), payout_route=route())
        packet = ac.compile_adjudication_collection(p)
        self.assertTrue(ac.verify_adjudication_collection(p, packet))
        altered = copy.deepcopy(packet)
        altered["authority"]["reward_offer_is_cash"] = True
        self.assertFalse(ac.verify_adjudication_collection(p, altered))

    def test_all_authority_expansion_flags_remain_false(self):
        packet = ac.compile_adjudication_collection(payload(add_offer(base_manifest()), payout_route=route()))
        auth = packet["authority"]
        for key, value in auth.items():
            if key == "owner_or_separately_authorized_sender_required":
                self.assertTrue(value)
            else:
                self.assertFalse(value, key)

    def test_load_json_rejects_duplicate_keys_and_floats(self):
        with tempfile.TemporaryDirectory() as tmp:
            dup = Path(tmp, "dup.json")
            dup.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            with self.assertRaisesRegex(ac.AdjudicationCollectionError, "duplicate JSON key"):
                ac.load_json(dup)
            flt = Path(tmp, "float.json")
            flt.write_text('{"x":1.2}', encoding="utf-8")
            with self.assertRaisesRegex(ac.AdjudicationCollectionError, "floats"):
                ac.load_json(flt)

    def test_load_json_rejects_non_regular_file_when_supported(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unsupported")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp, "pipe")
            os.mkfifo(fifo)
            with self.assertRaises(ac.AdjudicationCollectionError):
                ac.load_json(fifo)


if __name__ == "__main__":
    unittest.main()
