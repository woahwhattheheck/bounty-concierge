from __future__ import annotations

import copy
import hashlib
import json
import unittest

from concierge.payoff_path_gate import (
    CONTINUITY_SCHEMA,
    LEGACY_WORK_SCHEMA,
    WORK_SCHEMA,
    PayoffPathError,
    compile_gate,
    verify_gate,
)

AS_OF = "2026-09-13T15:00:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def payoff_path():
    return {
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "source": {
            "canonical_url": "https://example.com/opportunity/42",
            "evidence_ref": "source:terms-v3",
            "evidence_sha256": SHA_A,
            "observed_at_utc": "2026-09-13T14:00:00.000Z",
            "max_age_days": 7,
        },
        "conversion": {
            "event": "SUBMIT_WORK",
            "due_at_utc": "2026-09-20T12:00:00.000Z",
            "evidence_ref": "conversion:deadline-v1",
            "evidence_sha256": SHA_B,
        },
    }


def work_item(*, spent=45, budget=300):
    return {
        "work_id": "work-1",
        "opportunity_id": "opp-42",
        "started_at_utc": "2026-09-13T13:00:00.000Z",
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": payoff_path(),
    }


def event(event_id, kind, minutes, *, work_id=None, at="2026-09-13T13:30:00.000Z"):
    return {
        "event_id": event_id,
        "kind": kind,
        "opportunity_id": "opp-42",
        "work_id": work_id,
        "minutes": minutes,
        "occurred_at_utc": at,
    }


def document(events, *, generation=0, previous_receipt_sha256=None, spent=45, budget=300):
    return {
        "schema": WORK_SCHEMA,
        "continuity": {
            "schema": CONTINUITY_SCHEMA,
            "ledger_id": "owner-free-work-ledger",
            "generation": generation,
            "previous_receipt_sha256": previous_receipt_sha256,
            "events": events,
        },
        "work_items": [work_item(spent=spent, budget=budget)],
    }


class PayoffPathContinuityTests(unittest.TestCase):
    def setUp(self):
        self.base_events = [
            event("budget-1", "BUDGET_SET", 300, at="2026-09-13T13:00:00.000Z"),
            event("effort-1", "EFFORT", 45, work_id="work-1"),
        ]
        self.doc0 = document(copy.deepcopy(self.base_events))
        self.packet0, self.markdown0, self.receipt0 = compile_gate(self.doc0, AS_OF)

    def test_generation_zero_is_chained_and_verifiable(self):
        self.assertEqual("CHAINED", self.packet0["continuity"]["mode"])
        self.assertEqual(45, self.packet0["results"][0]["free_work_spent_minutes"])
        self.assertEqual(
            "READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW",
            self.packet0["results"][0]["state"],
        )
        self.assertTrue(
            verify_gate(self.doc0, self.packet0, self.markdown0, self.receipt0, AS_OF)
        )

    def generation_one(self, *, spent=65):
        events = copy.deepcopy(self.base_events)
        events.append(
            event(
                "effort-2",
                "EFFORT",
                20,
                work_id="work-1",
                at="2026-09-13T14:30:00.000Z",
            )
        )
        return document(
            events,
            generation=1,
            previous_receipt_sha256=canonical_digest(self.receipt0),
            spent=spent,
        )

    def test_successor_accumulates_effort_across_packets(self):
        doc1 = self.generation_one()
        packet1, markdown1, receipt1 = compile_gate(doc1, AS_OF, self.receipt0)
        self.assertEqual(65, packet1["results"][0]["free_work_spent_minutes"])
        self.assertEqual(235, packet1["results"][0]["free_work_remaining_minutes"])
        self.assertTrue(
            verify_gate(doc1, packet1, markdown1, receipt1, AS_OF, self.receipt0)
        )

    def test_nonzero_generation_without_previous_receipt_fails_closed(self):
        with self.assertRaisesRegex(PayoffPathError, "previous receipt is required"):
            compile_gate(self.generation_one(), AS_OF)

    def test_wrong_previous_receipt_digest_fails_closed(self):
        doc1 = self.generation_one()
        doc1["continuity"]["previous_receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(PayoffPathError, "digest does not match"):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_truncated_history_fails_closed(self):
        doc1 = self.generation_one()
        doc1["continuity"]["events"] = doc1["continuity"]["events"][1:]
        with self.assertRaises(PayoffPathError):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_rewritten_history_prefix_fails_closed(self):
        doc1 = self.generation_one(spent=64)
        doc1["continuity"]["events"][1]["minutes"] = 44
        with self.assertRaisesRegex(PayoffPathError, "prefix"):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_spend_reset_assertion_fails_closed(self):
        doc1 = self.generation_one(spent=1)
        with self.assertRaisesRegex(PayoffPathError, "spent minutes"):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_budget_increase_assertion_fails_closed(self):
        doc1 = self.generation_one()
        doc1["work_items"][0]["free_work_budget_minutes"] = 400
        with self.assertRaisesRegex(PayoffPathError, "budget"):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_duplicate_event_id_replay_fails_closed(self):
        doc1 = self.generation_one(spent=85)
        doc1["continuity"]["events"].append(
            copy.deepcopy(doc1["continuity"]["events"][-1])
        )
        with self.assertRaisesRegex(PayoffPathError, "duplicate continuity event_id"):
            compile_gate(doc1, AS_OF, self.receipt0)

    def test_cumulative_effort_reaches_cap_and_stops(self):
        doc1 = self.generation_one()
        packet1, _, receipt1 = compile_gate(doc1, AS_OF, self.receipt0)
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", packet1["results"][0]["state"])
        events2 = copy.deepcopy(doc1["continuity"]["events"])
        events2.append(
            event(
                "effort-3",
                "EFFORT",
                235,
                work_id="work-1",
                at="2026-09-13T14:45:00.000Z",
            )
        )
        doc2 = document(
            events2,
            generation=2,
            previous_receipt_sha256=canonical_digest(receipt1),
            spent=300,
        )
        packet2, _, _ = compile_gate(doc2, AS_OF, receipt1)
        self.assertEqual("STOP_UNPAID_WORK", packet2["results"][0]["state"])
        self.assertEqual(0, packet2["results"][0]["free_work_remaining_minutes"])

    def test_legacy_production_input_cannot_yield_ready(self):
        legacy = {"schema": LEGACY_WORK_SCHEMA, "work_items": [work_item()]}
        packet, _, _ = compile_gate(legacy)
        self.assertEqual("MISSING_HISTORY_FAIL_CLOSED", packet["continuity"]["mode"])
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("CONTINUITY_HISTORY_REQUIRED", packet["results"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
