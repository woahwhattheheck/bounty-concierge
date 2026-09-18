# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import hmac
from pathlib import Path
import unittest

from concierge.accepted_work_collection_bridge import (
    ACCEPTANCE_SCHEMA,
    AcceptedWorkBridgeError,
    build_bridge,
    bridge_bytes,
    compile_bytes,
    verify_bridge,
)
from concierge.reward_settlement_certifier import REGISTRY_SCHEMA, canonical
from concierge.reward_settlement_ledger import load_json_bytes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "reward_settlement_ledger.synthetic.json"
KEY = b"accepted-work-bridge-test-secret-32"
AS_OF = "2026-09-17T00:31:00Z"
FRESHNESS = 3600


def fixture_doc(*case_ids: str):
    doc = load_json_bytes(FIXTURE.read_bytes())
    if case_ids:
        wanted = set(case_ids)
        doc["cases"] = [copy.deepcopy(case) for case in doc["cases"] if case["case_id"] in wanted]
    return doc


def acceptance_source(case_id: str, observed_at: str = "2026-09-17T00:10:00Z", authority: str = "SPONSOR"):
    suffix = case_id.replace("synthetic-", "")
    return {
        "source_id": f"accept-{suffix}",
        "source_ref": f"fixture://acceptance/{suffix}",
        "source_sha256": hashlib.sha256(f"accepted:{case_id}".encode()).hexdigest(),
        "observed_at": observed_at,
        "authority": authority,
    }


def acceptance_manifest(doc, *, case_ids=None, observed_at="2026-09-17T00:10:00Z", authority="SPONSOR"):
    wanted = set(
        case_ids
        if case_ids is not None
        else [case["case_id"] for case in doc["cases"]]
    )
    rows = []
    for case in doc["cases"]:
        if case["case_id"] not in wanted:
            continue
        rows.append({
            "case_id": case["case_id"],
            "work": {
                "repo": case["work"]["repo"],
                "pr": case["work"]["pr"],
                "merge_commit_sha": case["work"]["merge_commit_sha"],
            },
            "decision": "ACCEPTED",
            "source": acceptance_source(case["case_id"], observed_at, authority),
        })
    return {
        "schema": ACCEPTANCE_SCHEMA,
        "generated_at": "2026-09-17T00:20:00Z",
        "records": rows,
    }


def signed_registry(doc, acceptance, *, include_acceptance=True, generated_at="2026-09-17T00:30:00Z"):
    by_id = {}
    for case in doc["cases"]:
        sources = [case["work"]["source"], *[event["source"] for event in case["events"]]]
        for source in sources:
            by_id[source["source_id"]] = copy.deepcopy(source)
    if include_acceptance:
        for row in acceptance["records"]:
            source = row["source"]
            by_id[source["source_id"]] = copy.deepcopy(source)
    body = {
        "schema": REGISTRY_SCHEMA,
        "generated_at": generated_at,
        "sources": [by_id[key] for key in sorted(by_id)],
    }
    return {
        **body,
        "signature_hmac_sha256": hmac.new(KEY, canonical(body), hashlib.sha256).hexdigest(),
    }


def one_record(case_id, *, include_acceptance=True, trust_acceptance=True, observed_at="2026-09-17T00:10:00Z", as_of=AS_OF, freshness=FRESHNESS):
    doc = fixture_doc(case_id)
    acceptance = acceptance_manifest(
        doc,
        case_ids=[case_id] if include_acceptance else [],
        observed_at=observed_at,
    )
    registry = signed_registry(doc, acceptance, include_acceptance=trust_acceptance)
    bridge = build_bridge(
        doc, registry, KEY, acceptance, as_of=as_of, freshness_seconds=freshness
    )
    return bridge, bridge["records"][0], doc, registry, acceptance


class AcceptedWorkCollectionBridgeTests(unittest.TestCase):
    def test_accepted_award_chain_becomes_explicit_unpaid_collections_review(self):
        bridge, row, *_ = one_record("synthetic-award-ticket-rail")
        self.assertEqual(row["settlement_queue_state"], "PAYOUT_RAIL_FOLLOWUP_CANDIDATE")
        self.assertEqual(row["work_acceptance"]["status"], "WORK_ACCEPTED_SOURCE_CERTIFIED")
        self.assertEqual(row["finish_state"], "ACCEPTED_UNPAID_COLLECTIONS_REVIEW")
        self.assertEqual(bridge["aggregates"]["accepted_unpaid_review_count"], 1)

    def test_accepted_merge_without_certified_award_is_not_promoted_to_collection_candidate(self):
        _, row, *_ = one_record("synthetic-advertised-only")
        self.assertEqual(row["settlement_queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertEqual(row["finish_state"], "ACCEPTED_UNPAID_SETTLEMENT_EVIDENCE_REVIEW")
        self.assertIn("SETTLEMENT_NEEDS_TRUST_EVIDENCE", row["finish_reason_codes"])

    def test_paid_and_closed_remain_terminal_even_with_acceptance_evidence(self):
        _, paid, *_ = one_record("synthetic-paid")
        _, closed, *_ = one_record("synthetic-closed-no-reward")
        self.assertEqual(paid["finish_state"], "PAID_CLOSED")
        self.assertEqual(closed["finish_state"], "CLOSED_NO_REWARD")

    def test_bare_merge_stays_distinct_from_explicit_acceptance(self):
        _, row, *_ = one_record("synthetic-award-ticket-rail", include_acceptance=False)
        self.assertEqual(row["work_acceptance"]["status"], "ACCEPTANCE_NOT_EVIDENCED")
        self.assertEqual(row["finish_state"], "MERGED_WITHOUT_ACCEPTANCE_EVIDENCE")

    def test_untrusted_acceptance_never_promotes_finish_state(self):
        _, row, *_ = one_record("synthetic-award-ticket-rail", trust_acceptance=False)
        self.assertEqual(row["work_acceptance"]["status"], "WORK_ACCEPTANCE_SOURCE_UNTRUSTED")
        self.assertEqual(row["finish_state"], "ACCEPTANCE_NEEDS_TRUST_EVIDENCE")

    def test_stale_acceptance_never_promotes_finish_state(self):
        _, row, *_ = one_record(
            "synthetic-award-ticket-rail",
            observed_at="2026-09-17T00:10:00Z",
            as_of="2026-09-17T01:10:01Z",
            freshness=3600,
        )
        self.assertEqual(row["finish_state"], "ACCEPTANCE_NEEDS_REFRESH")
        self.assertIn("WORK_ACCEPTANCE_EVIDENCE_STALE_AT_AS_OF", row["finish_reason_codes"])

    def test_repository_authority_cannot_be_relabeled_as_commercial_acceptance(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc, authority="REPOSITORY")
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_pre_merge_acceptance_evidence_is_rejected(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc, observed_at="2026-09-16T22:59:59Z")
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_acceptance_must_bind_exact_repo_pr_and_merge_sha(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        acceptance["records"][0]["work"]["merge_commit_sha"] = "f" * 40
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_duplicate_acceptance_for_one_case_fails_closed(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        duplicate = copy.deepcopy(acceptance["records"][0])
        duplicate["source"] = acceptance_source(
            duplicate["case_id"], observed_at="2026-09-17T00:11:00Z", authority="PROVIDER"
        )
        duplicate["source"]["source_id"] += "-2"
        acceptance["records"].append(duplicate)
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_cross_case_acceptance_source_reuse_fails_closed(self):
        doc = fixture_doc("synthetic-advertised-only", "synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        acceptance["records"][1]["source"] = copy.deepcopy(acceptance["records"][0]["source"])
        registry = signed_registry(doc, acceptance)
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_manifest_cannot_postdate_signed_registry_generation(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        acceptance["generated_at"] = "2026-09-17T00:30:01Z"
        registry = signed_registry(doc, acceptance, generated_at="2026-09-17T00:30:00Z")
        with self.assertRaises(AcceptedWorkBridgeError):
            build_bridge(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)

    def test_strict_byte_entrypoint_rejects_duplicate_json_keys(self):
        _, _, doc, registry, acceptance = one_record("synthetic-award-ticket-rail")
        with self.assertRaises(AcceptedWorkBridgeError):
            compile_bytes(
                canonical(doc),
                canonical(registry),
                b'{"schema":"x","schema":"y"}',
                KEY,
                as_of=AS_OF,
                freshness_seconds=FRESHNESS,
            )
        good = compile_bytes(
            canonical(doc),
            canonical(registry),
            canonical(acceptance),
            KEY,
            as_of=AS_OF,
            freshness_seconds=FRESHNESS,
        )
        self.assertEqual(
            good,
            bridge_bytes(
                doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS
            ),
        )

    def test_output_is_deterministic_and_input_order_independent(self):
        doc = fixture_doc("synthetic-advertised-only", "synthetic-award-ticket-rail")
        acceptance = acceptance_manifest(doc)
        registry = signed_registry(doc, acceptance)
        first = bridge_bytes(doc, registry, KEY, acceptance, as_of=AS_OF, freshness_seconds=FRESHNESS)
        reversed_doc = copy.deepcopy(doc)
        reversed_doc["cases"].reverse()
        reversed_acceptance = copy.deepcopy(acceptance)
        reversed_acceptance["records"].reverse()
        second = bridge_bytes(
            reversed_doc,
            registry,
            KEY,
            reversed_acceptance,
            as_of=AS_OF,
            freshness_seconds=FRESHNESS,
        )
        self.assertEqual(first, second)

    def test_authority_is_hard_false_and_receipt_recomputes(self):
        bridge, _, doc, registry, acceptance = one_record("synthetic-award-ticket-rail")
        self.assertTrue(all(value is False for value in bridge["authority"].values()))
        self.assertEqual(len(bridge["receipt_sha256"]), 64)
        self.assertTrue(
            verify_bridge(
                doc,
                registry,
                KEY,
                acceptance,
                bridge,
                as_of=AS_OF,
                freshness_seconds=FRESHNESS,
            )
        )
        tampered = copy.deepcopy(bridge)
        tampered["authority"]["send_outbound"] = True
        self.assertFalse(
            verify_bridge(
                doc,
                registry,
                KEY,
                acceptance,
                tampered,
                as_of=AS_OF,
                freshness_seconds=FRESHNESS,
            )
        )


if __name__ == "__main__":
    unittest.main()
