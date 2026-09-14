from __future__ import annotations

import copy
import hashlib
import json
import unittest

from concierge.payoff_path_gate import (
    BUDGET_POLICY_KIND,
    CONTINUITY_SCHEMA,
    CONTINUITY_SEMANTICS_V2,
    WORK_SCHEMA,
    PayoffPathError,
    compile_gate,
    verify_gate,
)

AS_OF = "2026-09-13T15:30:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def path():
    return {
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "source": {
            "canonical_url": "https://example.com/bounty/42",
            "evidence_ref": "source:terms-v4",
            "evidence_sha256": SHA_A,
            "observed_at_utc": "2026-09-13T12:00:00.000Z",
            "max_age_days": 7,
        },
        "conversion": {
            "event": "SUBMIT_WORK",
            "due_at_utc": "2026-09-20T12:00:00.000Z",
            "evidence_ref": "conversion:deadline-v2",
            "evidence_sha256": SHA_B,
        },
    }


def work(*, budget, spent, work_id="work-1", opp="opp-42"):
    return {
        "work_id": work_id,
        "opportunity_id": opp,
        "started_at_utc": "2026-09-13T12:30:00.000Z",
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": path(),
    }


def legacy_budget(minutes=60, *, event_id="budget-0", opp="opp-42", at="2026-09-13T13:00:00.000Z"):
    return {
        "event_id": event_id,
        "kind": "BUDGET_SET",
        "opportunity_id": opp,
        "work_id": None,
        "minutes": minutes,
        "occurred_at_utc": at,
    }


def policy(generation, minutes, *, predecessor=None, event_id=None, opp="opp-42", at=None):
    if event_id is None:
        event_id = f"policy-{generation}"
    if at is None:
        at = f"2026-09-13T{13 + generation:02d}:00:00.000Z"
    return {
        "event_id": event_id,
        "kind": BUDGET_POLICY_KIND,
        "opportunity_id": opp,
        "work_id": None,
        "minutes": minutes,
        "occurred_at_utc": at,
        "evidence_ref": f"owner-policy:{generation}",
        "evidence_sha256": SHA_C if generation % 2 == 0 else SHA_D,
        "policy_generation": generation,
        "supersedes_policy_sha256": predecessor,
    }


def effort(minutes, *, event_id="effort-1", work_id="work-1", opp="opp-42", at="2026-09-13T13:30:00.000Z", evidenced=True):
    event = {
        "event_id": event_id,
        "kind": "EFFORT",
        "opportunity_id": opp,
        "work_id": work_id,
        "minutes": minutes,
        "occurred_at_utc": at,
    }
    if evidenced:
        event.update({"evidence_ref": f"effort-proof:{event_id}", "evidence_sha256": SHA_D})
    return event


def document(events, *, budget, spent, generation=0, previous=None, work_id="work-1"):
    return {
        "schema": WORK_SCHEMA,
        "continuity": {
            "schema": CONTINUITY_SCHEMA,
            "ledger_id": "owner-free-work-ledger",
            "generation": generation,
            "previous_receipt_sha256": previous,
            "events": events,
        },
        "work_items": [work(budget=budget, spent=spent, work_id=work_id)],
    }


class EvidenceBoundContinuityTests(unittest.TestCase):
    def test_extended_generation_zero_is_evidence_bound_and_verifiable(self):
        p0 = policy(0, 120)
        events = [p0, effort(25)]
        doc = document(events, budget=120, spent=25)
        packet, markdown, receipt = compile_gate(doc, AS_OF)
        self.assertEqual(CONTINUITY_SEMANTICS_V2, packet["continuity"]["semantics"])
        self.assertEqual(1, packet["continuity"]["evidence_bound_effort_event_count"])
        self.assertEqual(1, packet["continuity"]["owner_policy_event_count"])
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", packet["results"][0]["state"])
        self.assertIn("Continuity policy custody", markdown)
        self.assertEqual(
            {
                "mode", "ledger_id", "generation", "ledger_event_count",
                "ledger_root_sha256", "previous_receipt_sha256",
            },
            set(receipt["continuity"]),
        )
        self.assertTrue(verify_gate(doc, packet, markdown, receipt, AS_OF))

    def test_legacy_receipt_migrates_and_explicit_owner_policy_reopens_stop(self):
        b0 = legacy_budget(60)
        e0 = effort(60, evidenced=False)
        doc0 = document([b0, e0], budget=60, spent=60)
        packet0, _, receipt0 = compile_gate(doc0, AS_OF)
        self.assertEqual("STOP_UNPAID_WORK", packet0["results"][0]["state"])
        self.assertNotIn("semantics", packet0["continuity"])

        p1 = policy(1, 120, predecessor=digest(b0), at="2026-09-13T14:00:00.000Z")
        doc1 = document(
            [b0, e0, p1], budget=120, spent=60, generation=1, previous=digest(receipt0)
        )
        packet1, markdown1, receipt1 = compile_gate(doc1, AS_OF, receipt0)
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", packet1["results"][0]["state"])
        self.assertTrue(packet1["continuity"]["budget_policy_history"][-1]["reopened_from_stop"])
        self.assertIn("STOP→reopen explicitly authorized", markdown1)
        self.assertTrue(verify_gate(doc1, packet1, markdown1, receipt1, AS_OF, receipt0))

    def test_same_generation_scalar_cap_increase_fails_without_policy_event(self):
        p0 = policy(0, 60)
        doc = document([p0, effort(20)], budget=120, spent=20)
        with self.assertRaisesRegex(PayoffPathError, "active owner budget policy"):
            compile_gate(doc, AS_OF)

    def test_spent_reset_fails_against_immutable_effort(self):
        p0 = policy(0, 120)
        doc = document([p0, effort(30)], budget=120, spent=0)
        with self.assertRaisesRegex(PayoffPathError, "cumulative continuity effort"):
            compile_gate(doc, AS_OF)

    def test_exact_same_id_replay_is_idempotent(self):
        p0 = policy(0, 120)
        e0 = effort(30)
        doc = document([p0, e0, copy.deepcopy(e0)], budget=120, spent=30)
        packet, _, _ = compile_gate(doc, AS_OF)
        self.assertEqual(2, packet["continuity"]["ledger_event_count"])
        self.assertEqual(30, packet["results"][0]["free_work_spent_minutes"])

    def test_changed_same_id_replay_fails(self):
        p0 = policy(0, 120)
        e0 = effort(30)
        changed = copy.deepcopy(e0)
        changed["minutes"] = 31
        doc = document([p0, e0, changed], budget=120, spent=61)
        with self.assertRaisesRegex(PayoffPathError, "changed immutable facts"):
            compile_gate(doc, AS_OF)

    def test_order_is_canonical_for_distinct_timestamps(self):
        p0 = policy(0, 120, at="2026-09-13T13:00:00.000Z")
        e0 = effort(20, at="2026-09-13T13:30:00.000Z")
        p1 = policy(1, 140, predecessor=digest(p0), at="2026-09-13T14:00:00.000Z")
        doc_a = document([p0, e0, p1], budget=140, spent=20)
        doc_b = document([p1, p0, e0], budget=140, spent=20)
        packet_a, _, _ = compile_gate(doc_a, AS_OF)
        packet_b, _, _ = compile_gate(doc_b, AS_OF)
        self.assertEqual(packet_a["continuity"]["ledger_root_sha256"], packet_b["continuity"]["ledger_root_sha256"])
        self.assertEqual(packet_a["results"], packet_b["results"])

    def test_wrong_policy_predecessor_digest_fails(self):
        p0 = policy(0, 120)
        p1 = policy(1, 180, predecessor="0" * 64)
        doc = document([p0, effort(20), p1], budget=180, spent=20)
        with self.assertRaisesRegex(PayoffPathError, "predecessor digest mismatch"):
            compile_gate(doc, AS_OF)

    def test_policy_can_tighten_cap_and_force_stop(self):
        p0 = policy(0, 120)
        e0 = effort(80)
        p1 = policy(1, 50, predecessor=digest(p0), at="2026-09-13T14:00:00.000Z")
        doc = document([p0, e0, p1], budget=50, spent=80)
        packet, _, _ = compile_gate(doc, AS_OF)
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])
        self.assertFalse(packet["continuity"]["budget_policy_history"][-1]["reopened_from_stop"])

    def test_cross_work_transplant_fails(self):
        p0 = policy(0, 120)
        transplanted = effort(20, work_id="work-other")
        doc = document([p0, transplanted], budget=120, spent=20)
        with self.assertRaisesRegex(PayoffPathError, "unknown work_id"):
            compile_gate(doc, AS_OF)

    def test_legacy_effort_after_owner_policy_migration_fails(self):
        b0 = legacy_budget(60)
        old = effort(20, evidenced=False, at="2026-09-13T13:30:00.000Z")
        p1 = policy(1, 120, predecessor=digest(b0), at="2026-09-13T14:00:00.000Z")
        unbound = effort(10, event_id="effort-unbound", evidenced=False, at="2026-09-13T14:30:00.000Z")
        doc = document([b0, old, p1, unbound], budget=120, spent=30)
        with self.assertRaisesRegex(PayoffPathError, "must be evidence-bound"):
            compile_gate(doc, AS_OF)

    def test_generation_omission_fails_previous_root_prefix(self):
        p0 = policy(0, 120)
        e0 = effort(20)
        doc0 = document([p0, e0], budget=120, spent=20)
        _, _, receipt0 = compile_gate(doc0, AS_OF)
        p1 = policy(1, 180, predecessor=digest(p0), at="2026-09-13T14:00:00.000Z")
        doc1 = document(
            [p0, p1], budget=180, spent=0, generation=1, previous=digest(receipt0)
        )
        with self.assertRaisesRegex(PayoffPathError, "prefix"):
            compile_gate(doc1, AS_OF, receipt0)

    def test_authority_ceiling_is_unchanged(self):
        p0 = policy(0, 120)
        doc = document([p0, effort(10)], budget=120, spent=10)
        packet, _, _ = compile_gate(doc, AS_OF)
        self.assertEqual("OWNER_REVIEW_ONLY_NO_EXTERNAL_ACTION", packet["authority"])


if __name__ == "__main__":
    unittest.main()
