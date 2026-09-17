# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import hashlib
import hmac
from pathlib import Path
import unittest

from concierge.certified_settlement_collection_queue import (
    CollectionQueueError,
    build_queue,
    compile_bytes,
    queue_bytes,
)
from concierge.reward_settlement_certifier import REGISTRY_SCHEMA, canonical
from concierge.reward_settlement_ledger import load_json_bytes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "reward_settlement_ledger.synthetic.json"
KEY = b"collection-queue-test-secret-32bytes"
AS_OF = "2026-09-17T00:31:00Z"


def fixture_doc(*case_ids: str):
    doc = load_json_bytes(FIXTURE.read_bytes())
    if case_ids:
        wanted = set(case_ids)
        doc["cases"] = [copy.deepcopy(case) for case in doc["cases"] if case["case_id"] in wanted]
    return doc


def signed_registry(doc, omit_source_ids=frozenset(), generated_at=None):
    by_id = {}
    for case in doc["cases"]:
        for source in [case["work"]["source"], *[event["source"] for event in case["events"]]]:
            if source["source_id"] not in omit_source_ids:
                by_id[source["source_id"]] = copy.deepcopy(source)
    body = {
        "schema": REGISTRY_SCHEMA,
        "generated_at": generated_at or doc["generated_at"],
        "sources": [by_id[key] for key in sorted(by_id)],
    }
    return {
        **body,
        "signature_hmac_sha256": hmac.new(KEY, canonical(body), hashlib.sha256).hexdigest(),
    }


def one_state(doc, *, registry=None, as_of=AS_OF, freshness=3600):
    queue = build_queue(doc, registry or signed_registry(doc), KEY, as_of=as_of, freshness_seconds=freshness)
    assert len(queue["records"]) == 1
    return queue["records"][0]


class CertifiedSettlementCollectionQueueTests(unittest.TestCase):
    def test_terminal_certified_paid_and_closure_are_terminal_queue_states(self):
        paid = one_state(fixture_doc("synthetic-paid"))
        closed = one_state(fixture_doc("synthetic-closed-no-reward"))
        self.assertEqual(paid["queue_state"], "SETTLED")
        self.assertEqual(paid["reason_codes"], ["CERTIFIED_CONFIRMED_TRANSFER"])
        self.assertEqual(closed["queue_state"], "CLOSED_NO_REWARD")
        self.assertEqual(closed["reason_codes"], ["CERTIFIED_NO_REWARD_CLOSURE"])
        self.assertTrue(all(src["certified"] for src in paid["driver_sources"]))

    def test_award_ticket_and_rail_progression_become_distinct_candidates(self):
        base = fixture_doc("synthetic-award-ticket-rail")
        award_doc = copy.deepcopy(base)
        award_doc["cases"][0]["events"] = [e for e in award_doc["cases"][0]["events"] if e["kind"] in {"ADVERTISED_BOUNTY", "SPONSOR_AWARD", "ELIGIBILITY"}]
        ticket_doc = copy.deepcopy(base)
        ticket_doc["cases"][0]["events"] = [e for e in ticket_doc["cases"][0]["events"] if e["kind"] != "PAYOUT_RAIL"]
        self.assertEqual(one_state(award_doc)["queue_state"], "AWARD_FOLLOWUP_CANDIDATE")
        self.assertEqual(one_state(ticket_doc)["queue_state"], "PAYOUT_TICKET_FOLLOWUP_CANDIDATE")
        rail = one_state(base)
        self.assertEqual(rail["queue_state"], "PAYOUT_RAIL_FOLLOWUP_CANDIDATE")
        self.assertTrue(rail["route_evidence"]["payout_rails"])

    def test_pending_transfer_is_followup_candidate_but_failed_transfer_is_hold(self):
        pending_doc = fixture_doc("synthetic-pending-transfer")
        pending = one_state(pending_doc)
        self.assertEqual(pending["queue_state"], "TRANSFER_PENDING_FOLLOWUP_CANDIDATE")
        self.assertEqual(pending["route_evidence"]["transfers"][-1]["status"], "PENDING")
        failed_doc = copy.deepcopy(pending_doc)
        next(e for e in failed_doc["cases"][0]["events"] if e["kind"] == "TRANSFER")["status"] = "FAILED"
        failed = one_state(failed_doc)
        self.assertEqual(failed["queue_state"], "HOLD_CONTRADICTION")
        self.assertIn("CERTIFIED_TRANSFER_FAILED_TERMINAL", failed["reason_codes"])

    def test_merge_or_advertisement_without_certified_award_needs_evidence(self):
        advertised = one_state(fixture_doc("synthetic-advertised-only"))
        self.assertEqual(advertised["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertIn("NO_CERTIFIED_SPONSOR_AWARD", advertised["reason_codes"])

    def test_untrusted_latest_stage_never_becomes_followup_candidate(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        rail = next(e for e in doc["cases"][0]["events"] if e["kind"] == "PAYOUT_RAIL")
        registry = signed_registry(doc, omit_source_ids={rail["source"]["source_id"]})
        row = one_state(doc, registry=registry)
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertIn("LATEST_DECLARED_STAGE_NOT_CERTIFIED", row["reason_codes"])

    def test_stale_registry_or_stage_evidence_requires_refresh_before_followup(self):
        doc = fixture_doc("synthetic-award-ticket-rail")
        row = one_state(doc, as_of="2026-09-18T00:30:00Z", freshness=60)
        self.assertEqual(row["queue_state"], "NEEDS_TRUST_EVIDENCE")
        self.assertIn("TRUST_REGISTRY_STALE_AT_AS_OF", row["reason_codes"])
        self.assertFalse(row["trusted_registry_current"])

    def test_terminal_certification_does_not_expire_into_collection_work(self):
        paid = one_state(fixture_doc("synthetic-paid"), as_of="2026-09-18T00:30:00Z", freshness=60)
        closed = one_state(fixture_doc("synthetic-closed-no-reward"), as_of="2026-09-18T00:30:00Z", freshness=60)
        self.assertEqual(paid["queue_state"], "SETTLED")
        self.assertEqual(closed["queue_state"], "CLOSED_NO_REWARD")
        self.assertFalse(paid["trusted_registry_current"])

    def test_money_and_route_bindings_are_explicit_and_owner_review_only(self):
        row = one_state(fixture_doc("synthetic-award-ticket-rail"))
        self.assertEqual(row["money"]["unit"], "minor")
        self.assertIsInstance(row["money"]["amount_minor"], int)
        self.assertEqual(row["money"]["currency"], "USD")
        self.assertTrue(row["owner_review_only"])
        source = row["driver_sources"][0]
        self.assertEqual(set(source), {"source_id", "source_ref", "source_sha256", "observed_at", "authority", "age_seconds", "current", "certified"})
        self.assertEqual(len(row["certificate_receipt_sha256"]), 64)
        self.assertEqual(len(row["trusted_registry_body_sha256"]), 64)

    def test_queue_is_deterministic_and_order_independent(self):
        doc = fixture_doc("synthetic-paid", "synthetic-award-ticket-rail", "synthetic-pending-transfer")
        registry = signed_registry(doc)
        first = queue_bytes(doc, registry, KEY, as_of=AS_OF, freshness_seconds=3600)
        reversed_doc = copy.deepcopy(doc)
        reversed_doc["cases"].reverse()
        second = queue_bytes(reversed_doc, registry, KEY, as_of=AS_OF, freshness_seconds=3600)
        self.assertEqual(first, second)
        queue = build_queue(doc, registry, KEY, as_of=AS_OF, freshness_seconds=3600)
        self.assertEqual(queue["records"][0]["queue_state"], "TRANSFER_PENDING_FOLLOWUP_CANDIDATE")
        self.assertEqual(queue["records"][-1]["queue_state"], "SETTLED")

    def test_authority_ceiling_is_hard_false(self):
        doc = fixture_doc("synthetic-paid")
        queue = build_queue(doc, signed_registry(doc), KEY, as_of=AS_OF, freshness_seconds=3600)
        self.assertTrue(all(value is False for value in queue["authority"].values()))
        self.assertEqual(len(queue["receipt_sha256"]), 64)

    def test_strict_byte_entrypoint_matches_object_entrypoint(self):
        doc = fixture_doc("synthetic-paid")
        registry = signed_registry(doc)
        expected = queue_bytes(doc, registry, KEY, as_of=AS_OF, freshness_seconds=3600)
        actual = compile_bytes(canonical(doc), canonical(registry), KEY, as_of=AS_OF, freshness_seconds=3600)
        self.assertEqual(actual, expected)
        with self.assertRaises(Exception):
            compile_bytes(b'{"schema":"x","schema":"y"}', canonical(registry), KEY, as_of=AS_OF, freshness_seconds=3600)

    def test_invalid_clock_or_policy_fails_closed(self):
        doc = fixture_doc("synthetic-paid")
        registry = signed_registry(doc)
        with self.assertRaises(CollectionQueueError):
            build_queue(doc, registry, KEY, as_of="2026-09-16T00:00:00Z", freshness_seconds=3600)
        with self.assertRaises(CollectionQueueError):
            build_queue(doc, registry, KEY, as_of=AS_OF, freshness_seconds=True)
        with self.assertRaises(CollectionQueueError):
            build_queue(doc, registry, KEY, as_of=AS_OF, freshness_seconds=0)


if __name__ == "__main__":
    unittest.main()
