from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from concierge import payoff_path_gate as gate

AS_OF = "2026-09-13T15:00:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def digest(value):
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
            "observed_at_utc": "2026-09-13T12:00:00.000Z",
            "max_age_days": 7,
        },
        "conversion": {
            "event": "SUBMIT_WORK",
            "due_at_utc": "2026-09-20T12:00:00.000Z",
            "evidence_ref": "conversion:deadline-v1",
            "evidence_sha256": SHA_B,
        },
    }


def policy(event_id, generation, cap, at, predecessor=None, evidence_sha=SHA_B):
    return {
        "event_id": event_id,
        "kind": "BUDGET_POLICY",
        "opportunity_id": "opp-42",
        "work_id": None,
        "minutes": cap,
        "occurred_at_utc": at,
        "evidence_ref": f"owner-policy:{generation}",
        "evidence_sha256": evidence_sha,
        "policy_generation": generation,
        "predecessor_policy_sha256": predecessor,
    }


def effort(event_id, minutes, at, *, evidence_sha=SHA_C, work_id="work-1", opportunity_id="opp-42"):
    return {
        "event_id": event_id,
        "kind": "EFFORT",
        "opportunity_id": opportunity_id,
        "work_id": work_id,
        "minutes": minutes,
        "occurred_at_utc": at,
        "evidence_ref": f"effort-receipt:{event_id}",
        "evidence_sha256": evidence_sha,
        "policy_generation": None,
        "predecessor_policy_sha256": None,
    }


def work_item(*, budget, spent, work_id="work-1", opportunity_id="opp-42"):
    return {
        "work_id": work_id,
        "opportunity_id": opportunity_id,
        "started_at_utc": "2026-09-13T12:30:00.000Z",
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": payoff_path(),
    }


def document(events, *, budget, spent, generation=0, previous_receipt_sha256=None, work_id="work-1"):
    return {
        "schema": gate.WORK_SCHEMA_V3,
        "continuity": {
            "schema": gate.CONTINUITY_SCHEMA_V2,
            "ledger_id": "owner-free-work-ledger-v3",
            "generation": generation,
            "previous_receipt_sha256": previous_receipt_sha256,
            "events": events,
        },
        "work_items": [work_item(budget=budget, spent=spent, work_id=work_id)],
    }


class PayoffPathPolicyV3Tests(unittest.TestCase):
    def setUp(self):
        # These are historical semantic fixtures for the landed v3 state machine,
        # not current authority tests. Production/current v3 never enables this
        # host-only switch; detached HMAC authority is covered separately.
        self._unsigned = patch.dict(
            os.environ,
            {gate.PAYOFF_POLICY_TEST_UNSIGNED_ENV: "1"},
            clear=False,
        )
        self._unsigned.start()
        self.addCleanup(self._unsigned.stop)

        self.p0 = policy(
            "policy-0", 0, 60, "2026-09-13T12:30:00.000Z", evidence_sha=SHA_B
        )
        self.e0 = effort("effort-0", 60, "2026-09-13T13:00:00.000Z")
        self.doc0 = document([copy.deepcopy(self.e0), copy.deepcopy(self.p0)], budget=60, spent=60)
        self.packet0, self.markdown0, self.receipt0 = gate.compile_gate(self.doc0, AS_OF)

    def successor(self, *, cap=120, spent=60, events=None):
        p1 = policy(
            "policy-1", 1, cap, "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(self.p0), evidence_sha=SHA_D,
        )
        if events is None:
            events = [copy.deepcopy(self.p0), copy.deepcopy(self.e0), p1]
        return document(
            events, budget=cap, spent=spent, generation=1,
            previous_receipt_sha256=digest(self.receipt0),
        )

    def test_generation_zero_stop_is_evidence_bound_and_verifiable(self):
        self.assertEqual("payoff-path-gate/v3", self.packet0["schema"])
        self.assertEqual("CHAINED", self.packet0["continuity"]["mode"])
        self.assertEqual("STOP_UNPAID_WORK", self.packet0["results"][0]["state"])
        self.assertIn("Continuity: CHAINED ledger", self.markdown0)
        self.assertNotIn("MISSING_HISTORY_FAIL_CLOSED", self.markdown0)
        self.assertTrue(
            gate.verify_gate(self.doc0, self.packet0, self.markdown0, self.receipt0, AS_OF)
        )

    def test_exact_event_replay_is_idempotent_and_order_invariant(self):
        replay = document(
            [copy.deepcopy(self.p0), copy.deepcopy(self.e0), copy.deepcopy(self.e0)],
            budget=60, spent=60,
        )
        packet, markdown, receipt = gate.compile_gate(replay, AS_OF)
        self.assertEqual(self.packet0, packet)
        self.assertEqual(self.markdown0, markdown)
        self.assertEqual(self.receipt0, receipt)

    def test_changed_same_event_id_fails_closed(self):
        changed = copy.deepcopy(self.e0)
        changed["evidence_sha256"] = SHA_D
        replay = document([self.p0, self.e0, changed], budget=60, spent=60)
        with self.assertRaisesRegex(gate.PayoffPathError, "changed bytes"):
            gate.compile_gate(replay, AS_OF)

    def test_explicit_successor_policy_can_visibly_reopen_prior_stop(self):
        doc1 = self.successor()
        packet1, markdown1, receipt1 = gate.compile_gate(doc1, AS_OF, self.receipt0)
        row = packet1["results"][0]
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", row["state"])
        self.assertIn("OWNER_POLICY_SUPERSESSION_REOPENED_AFTER_STOP", row["reasons"])
        self.assertEqual(1, row["owner_policy"]["policy_generation"])
        self.assertTrue(row["owner_policy"]["reopened_from_stop"])
        self.assertIn("Evidence-bound owner policy continuity", markdown1)
        self.assertTrue(gate.verify_gate(doc1, packet1, markdown1, receipt1, AS_OF, self.receipt0))

    def test_policy_tightening_below_spend_stays_stop(self):
        doc1 = self.successor(cap=30)
        packet1, _, _ = gate.compile_gate(doc1, AS_OF, self.receipt0)
        self.assertEqual("STOP_UNPAID_WORK", packet1["results"][0]["state"])
        self.assertEqual(30, packet1["results"][0]["owner_policy"]["cap_minutes"])
        self.assertFalse(packet1["results"][0]["owner_policy"]["reopened_from_stop"])

    def test_policy_predecessor_mismatch_fails_closed(self):
        p1 = policy(
            "policy-1", 1, 120, "2026-09-13T14:00:00.000Z",
            predecessor="f" * 64, evidence_sha=SHA_D,
        )
        doc1 = self.successor(events=[self.p0, self.e0, p1])
        with self.assertRaisesRegex(gate.PayoffPathError, "predecessor digest mismatch"):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_old_effort_evidence_mutation_breaks_receipt_prefix(self):
        changed = copy.deepcopy(self.e0)
        changed["evidence_sha256"] = SHA_D
        p1 = policy(
            "policy-1", 1, 120, "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(self.p0), evidence_sha=SHA_D,
        )
        doc1 = self.successor(events=[self.p0, changed, p1])
        with self.assertRaisesRegex(gate.PayoffPathError, "prefix"):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_prior_policy_omission_fails_closed(self):
        p1 = policy(
            "policy-1", 1, 120, "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(self.p0), evidence_sha=SHA_D,
        )
        doc1 = self.successor(events=[self.e0, p1])
        with self.assertRaises(gate.PayoffPathError):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_same_generation_cap_rewrite_fails_closed(self):
        rewritten_p0 = copy.deepcopy(self.p0)
        rewritten_p0["minutes"] = 120
        p1 = policy(
            "policy-1", 1, 150, "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(rewritten_p0), evidence_sha=SHA_D,
        )
        doc1 = self.successor(cap=150, events=[rewritten_p0, self.e0, p1])
        with self.assertRaisesRegex(gate.PayoffPathError, "prefix"):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_cross_work_receipt_transplant_fails_closed(self):
        p1 = policy(
            "policy-1", 1, 120, "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(self.p0), evidence_sha=SHA_D,
        )
        e0_other = copy.deepcopy(self.e0)
        e0_other["work_id"] = "work-2"
        doc1 = document(
            [self.p0, e0_other, p1], budget=120, spent=60, generation=1,
            previous_receipt_sha256=digest(self.receipt0), work_id="work-2",
        )
        with self.assertRaisesRegex(gate.PayoffPathError, "scope changed"):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_reset_spend_assertion_cannot_reopen(self):
        doc1 = self.successor(spent=0)
        with self.assertRaisesRegex(gate.PayoffPathError, "spent minutes"):
            gate.compile_gate(doc1, AS_OF, self.receipt0)

    def test_new_effort_requires_immutable_evidence(self):
        bad = copy.deepcopy(self.e0)
        bad["evidence_sha256"] = None
        with self.assertRaises(gate.PayoffPathError):
            gate.compile_gate(document([self.p0, bad], budget=60, spent=60), AS_OF)

    def test_successor_is_invariant_to_input_event_order(self):
        canonical = self.successor()
        p1 = canonical["continuity"]["events"][-1]
        shuffled = self.successor(events=[p1, self.e0, self.p0])
        packet_a, markdown_a, receipt_a = gate.compile_gate(canonical, AS_OF, self.receipt0)
        packet_b, markdown_b, receipt_b = gate.compile_gate(shuffled, AS_OF, self.receipt0)
        self.assertEqual(packet_a, packet_b)
        self.assertEqual(markdown_a, markdown_b)
        self.assertEqual(receipt_a, receipt_b)

    def test_normal_v1_trusted_time_remains_fail_closed(self):
        legacy = {"schema": gate.LEGACY_WORK_SCHEMA, "work_items": [work_item(budget=60, spent=0)]}
        packet, _, _ = gate.compile_gate(legacy, AS_OF)
        self.assertEqual("MISSING_HISTORY_FAIL_CLOSED", packet["continuity"]["mode"])
        self.assertEqual("HOLD_STALE_OR_INVALID", packet["results"][0]["state"])
        self.assertIn("CONTINUITY_HISTORY_REQUIRED", packet["results"][0]["reasons"])

    def test_legacy_ready_exists_only_on_explicit_migration_surface(self):
        legacy = {"schema": gate.LEGACY_WORK_SCHEMA, "work_items": [work_item(budget=60, spent=0)]}
        packet, markdown, receipt = gate.compile_legacy_migration_gate(legacy, AS_OF)
        self.assertEqual("LEGACY_REPLAY_ONLY", packet["continuity"]["mode"])
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", packet["results"][0]["state"])
        self.assertTrue(gate.verify_legacy_migration_gate(legacy, packet, markdown, receipt, AS_OF))
        with self.assertRaisesRegex(gate.PayoffPathError, "migration-only"):
            gate.verify_gate(legacy, packet, markdown, receipt, AS_OF)


if __name__ == "__main__":
    unittest.main()
