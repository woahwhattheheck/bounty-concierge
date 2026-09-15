from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from concierge import payoff_path_gate as gate
from concierge import payoff_path_policy_authority as policy_authority
from concierge import payoff_path_policy_v3 as v3

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
KEY_HEX = "11" * 32
PRINCIPAL = "22" * 32
FORMER_UNSIGNED_ENV = "BOUNTY_PAYOFF_POLICY_TEST_ONLY_ALLOW_UNSIGNED"


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stamp(value: datetime) -> str:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def payoff_path(now: datetime):
    return {
        "mechanism": "BOUNTY",
        "value": {"kind": "FIXED", "currency": "USD", "amount_minor": 9000},
        "source": {
            "canonical_url": "https://example.com/opportunity/42",
            "evidence_ref": "source:terms-current",
            "evidence_sha256": SHA_A,
            "observed_at_utc": stamp(now - timedelta(minutes=2)),
            "max_age_days": 7,
        },
        "conversion": {
            "event": "SUBMIT_WORK",
            "due_at_utc": stamp(now + timedelta(days=1)),
            "evidence_ref": "conversion:deadline-current",
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
        "occurred_at_utc": stamp(at),
        "evidence_ref": f"owner-policy:{generation}",
        "evidence_sha256": evidence_sha,
        "policy_generation": generation,
        "predecessor_policy_sha256": predecessor,
    }


def effort(event_id, minutes, at, *, work_id="work-1", opportunity_id="opp-42"):
    return {
        "event_id": event_id,
        "kind": "EFFORT",
        "opportunity_id": opportunity_id,
        "work_id": work_id,
        "minutes": minutes,
        "occurred_at_utc": stamp(at),
        "evidence_ref": f"effort-receipt:{event_id}",
        "evidence_sha256": SHA_C,
        "policy_generation": None,
        "predecessor_policy_sha256": None,
    }


def work_item(now, *, budget, spent, work_id="work-1", opportunity_id="opp-42"):
    return {
        "work_id": work_id,
        "opportunity_id": opportunity_id,
        "started_at_utc": stamp(now - timedelta(seconds=110)),
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": payoff_path(now),
    }


def document(
    now,
    events,
    *,
    budget,
    spent,
    generation=0,
    previous_receipt_sha256=None,
    work_id="work-1",
):
    return {
        "schema": gate.WORK_SCHEMA_V3,
        "continuity": {
            "schema": gate.CONTINUITY_SCHEMA_V2,
            "ledger_id": "owner-free-work-ledger-current",
            "generation": generation,
            "previous_receipt_sha256": previous_receipt_sha256,
            "events": events,
        },
        "work_items": [
            work_item(now, budget=budget, spent=spent, work_id=work_id)
        ],
    }


class PayoffPathPolicyAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.p0 = policy(
            "policy-0",
            0,
            60,
            self.now - timedelta(seconds=90),
        )
        self.e0 = effort(
            "effort-0",
            60,
            self.now - timedelta(seconds=60),
        )
        self.doc0 = document(
            self.now,
            [copy.deepcopy(self.e0), copy.deepcopy(self.p0)],
            budget=60,
            spent=60,
        )
        self._env = patch.dict(
            os.environ,
            {
                gate.PAYOFF_POLICY_HMAC_KEY_ENV: KEY_HEX,
                gate.PAYOFF_POLICY_PROVIDER_ENV: "owner-host",
                gate.PAYOFF_POLICY_PRINCIPAL_ENV: PRINCIPAL,
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop(gate.PAYOFF_POLICY_CAPTURED_AT_ENV, None)
        os.environ.pop(gate.PAYOFF_POLICY_SIGNATURE_ENV, None)
        os.environ.pop(FORMER_UNSIGNED_ENV, None)

    def _authorize(self, doc, captured=None):
        values = policy_authority._sign_current_policy_authority_for_host_fixture(
            doc,
            captured_at_utc=stamp(captured or self.now),
        )
        os.environ.update(values)
        return values

    def _compile_generation_zero(self):
        self._authorize(self.doc0)
        packet, markdown, receipt = gate.compile_gate(self.doc0)
        self.assertEqual("STOP_UNPAID_WORK", packet["results"][0]["state"])
        return packet, markdown, receipt

    def _successor(self, receipt0, *, cap=120, work_id="work-1"):
        p1 = policy(
            "policy-1",
            1,
            cap,
            self.now - timedelta(seconds=30),
            predecessor=gate.POLICY_EVENT_SHA256(self.p0),
            evidence_sha=SHA_D,
        )
        events = [copy.deepcopy(self.p0), copy.deepcopy(self.e0), p1]
        if work_id != "work-1":
            events[1]["work_id"] = work_id
        return document(
            self.now,
            events,
            budget=cap,
            spent=60,
            generation=1,
            previous_receipt_sha256=canonical_digest(receipt0),
            work_id=work_id,
        )

    def test_current_v3_rejects_without_detached_host_signature(self):
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.compile_gate(self.doc0)

    def test_fixture_signer_is_not_exported_through_gate(self):
        self.assertFalse(hasattr(gate, "sign_current_policy_authority_for_host_fixture"))

    def test_removed_unsigned_env_name_cannot_disable_authority(self):
        os.environ[FORMER_UNSIGNED_ENV] = "1"
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.compile_gate(self.doc0)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.verify_gate(self.doc0, {}, "", {})

    def test_caller_selected_trusted_clock_does_not_bypass_authority(self):
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.compile_gate(self.doc0, stamp(self.now))
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            v3._compile_v3(self.doc0, stamp(self.now), None)

    def test_valid_host_authority_allows_current_generation_zero_stop(self):
        packet, markdown, receipt = self._compile_generation_zero()
        self.assertIn("Continuity: CHAINED ledger", markdown)
        self.assertTrue(gate.verify_gate(self.doc0, packet, markdown, receipt))

    def test_valid_signed_successor_can_visibly_reopen_prior_stop(self):
        _, _, receipt0 = self._compile_generation_zero()
        doc1 = self._successor(receipt0)
        self._authorize(doc1)
        packet1, markdown1, receipt1 = gate.compile_gate(
            doc1,
            previous_receipt=receipt0,
        )
        row = packet1["results"][0]
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", row["state"])
        self.assertIn("OWNER_POLICY_SUPERSESSION_REOPENED_AFTER_STOP", row["reasons"])
        self.assertTrue(
            gate.verify_gate(
                doc1,
                packet1,
                markdown1,
                receipt1,
                previous_receipt=receipt0,
            )
        )

    def test_self_consistent_fake_successor_cannot_mint_reopen(self):
        _, _, receipt0 = self._compile_generation_zero()
        signed = self._successor(receipt0, cap=120)
        self._authorize(signed)
        forged = self._successor(receipt0, cap=600)
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(forged, previous_receipt=receipt0)

    def test_policy_evidence_mutation_invalidates_host_signature(self):
        _, _, receipt0 = self._compile_generation_zero()
        doc1 = self._successor(receipt0)
        self._authorize(doc1)
        forged = copy.deepcopy(doc1)
        forged["continuity"]["events"][-1]["evidence_sha256"] = SHA_A
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(forged, previous_receipt=receipt0)

    def test_signature_cannot_transplant_to_other_work_scope(self):
        self._authorize(self.doc0)
        transplanted = copy.deepcopy(self.doc0)
        transplanted["work_items"][0]["work_id"] = "work-2"
        transplanted["continuity"]["events"][0]["work_id"] = "work-2"
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(transplanted)

    def test_policy_scope_is_event_order_invariant(self):
        reordered = copy.deepcopy(self.doc0)
        reordered["continuity"]["events"] = list(
            reversed(reordered["continuity"]["events"])
        )
        self.assertEqual(
            gate.PAYOFF_POLICY_AUTHORITY_SCOPE_SHA256(self.doc0),
            gate.PAYOFF_POLICY_AUTHORITY_SCOPE_SHA256(reordered),
        )

    def test_stale_host_authority_fails_closed(self):
        self._authorize(
            self.doc0,
            captured=self.now
            - timedelta(seconds=gate.PAYOFF_POLICY_AUTHORITY_MAX_AGE_SECONDS + 1),
        )
        with self.assertRaisesRegex(gate.PayoffPathError, "authority capture is stale"):
            gate.compile_gate(self.doc0)

    def test_future_host_authority_fails_closed(self):
        self._authorize(self.doc0, captured=self.now + timedelta(seconds=60))
        with self.assertRaisesRegex(gate.PayoffPathError, "capture is in the future"):
            gate.compile_gate(self.doc0)

    def test_host_identity_rotation_invalidates_existing_signature(self):
        self._authorize(self.doc0)
        os.environ[gate.PAYOFF_POLICY_PRINCIPAL_ENV] = "33" * 32
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(self.doc0)

    def test_current_verify_reacquires_host_authority(self):
        packet, markdown, receipt = self._compile_generation_zero()
        captured = os.environ.pop(gate.PAYOFF_POLICY_CAPTURED_AT_ENV)
        signature = os.environ.pop(gate.PAYOFF_POLICY_SIGNATURE_ENV)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.verify_gate(self.doc0, packet, markdown, receipt)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.verify_gate(self.doc0, packet, markdown, receipt, stamp(self.now))
        os.environ[gate.PAYOFF_POLICY_CAPTURED_AT_ENV] = captured
        os.environ[gate.PAYOFF_POLICY_SIGNATURE_ENV] = signature
        self.assertTrue(gate.verify_gate(self.doc0, packet, markdown, receipt))

    def test_direct_v3_current_compile_is_guarded(self):
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            v3._compile_v3(self.doc0, None, None)

    def test_signature_scope_binds_previous_receipt_anchor(self):
        _, _, receipt0 = self._compile_generation_zero()
        doc1 = self._successor(receipt0)
        self._authorize(doc1)
        forged = copy.deepcopy(doc1)
        forged["continuity"]["previous_receipt_sha256"] = "f" * 64
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(forged, previous_receipt=receipt0)


if __name__ == "__main__":
    unittest.main()
