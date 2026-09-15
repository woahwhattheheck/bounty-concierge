from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from concierge import payoff_path_gate as gate
from concierge import payoff_path_policy_authority as authority
from concierge import payoff_path_policy_v3 as v3
from concierge import payoff_path_gate_core as core

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


def effort(event_id, minutes, at):
    return {
        "event_id": event_id,
        "kind": "EFFORT",
        "opportunity_id": "opp-42",
        "work_id": "work-1",
        "minutes": minutes,
        "occurred_at_utc": at,
        "evidence_ref": f"effort-receipt:{event_id}",
        "evidence_sha256": SHA_C,
        "policy_generation": None,
        "predecessor_policy_sha256": None,
    }


def work_item(*, budget, spent):
    return {
        "work_id": "work-1",
        "opportunity_id": "opp-42",
        "started_at_utc": "2026-09-13T12:30:00.000Z",
        "free_work_budget_minutes": budget,
        "free_work_spent_minutes": spent,
        "payoff_path": payoff_path(),
    }


def document(events, *, budget, spent, generation=0, previous_receipt_sha256=None):
    return {
        "schema": gate.WORK_SCHEMA_V3,
        "continuity": {
            "schema": gate.CONTINUITY_SCHEMA_V2,
            "ledger_id": "owner-free-work-ledger-v3",
            "generation": generation,
            "previous_receipt_sha256": previous_receipt_sha256,
            "events": events,
        },
        "work_items": [work_item(budget=budget, spent=spent)],
    }


class PayoffPathPolicyAuthorityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                authority.KEY_ENV: "11" * 32,
                authority.PROVIDER_ENV: "fixture-provider",
                authority.PRINCIPAL_ENV: "9" * 64,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.p0 = policy("policy-0", 0, 60, "2026-09-13T12:30:00.000Z")
        self.e0 = effort("effort-0", 60, "2026-09-13T13:00:00.000Z")
        self.doc0 = document([copy.deepcopy(self.e0), copy.deepcopy(self.p0)], budget=60, spent=60)
        self.authorize(self.doc0)
        self.packet0, self.markdown0, self.receipt0 = gate.compile_gate(self.doc0, AS_OF)

        self.p1 = policy(
            "policy-1",
            1,
            120,
            "2026-09-13T14:00:00.000Z",
            predecessor=gate.POLICY_EVENT_SHA256(self.p0),
            evidence_sha=SHA_D,
        )
        self.doc1 = document(
            [copy.deepcopy(self.p0), copy.deepcopy(self.e0), copy.deepcopy(self.p1)],
            budget=120,
            spent=60,
            generation=1,
            previous_receipt_sha256=digest(self.receipt0),
        )
        self.authorize(self.doc1)
        self.packet1, self.markdown1, self.receipt1 = gate.compile_gate(
            self.doc1, AS_OF, self.receipt0
        )

    def authorize(self, document_value, *, captured_at_utc=None):
        signed = authority._sign_current_policy_authority_for_host_fixture(
            document_value,
            captured_at_utc=captured_at_utc,
        )
        os.environ.update(signed)
        return signed

    def clear_authority_receipt(self):
        os.environ.pop(authority.CAPTURED_AT_ENV, None)
        os.environ.pop(authority.SIGNATURE_ENV, None)

    def test_exact_detached_authority_allows_reopen_and_verification(self):
        self.assertEqual("READY_FOR_OWNER_SPECULATIVE_WORK_REVIEW", self.packet1["results"][0]["state"])
        self.assertTrue(
            gate.verify_gate(
                self.doc1,
                self.packet1,
                self.markdown1,
                self.receipt1,
                AS_OF,
                self.receipt0,
            )
        )
        payload = authority.verify_current_policy_authority(self.doc1)
        self.assertEqual(authority.AUTHORITY_PURPOSE, payload["purpose"])
        self.assertEqual(
            authority.policy_authority_scope_sha256(self.doc1),
            payload["policy_scope_sha256"],
        )

    def test_policy_mutation_and_host_identity_rotation_fail_closed(self):
        forged = copy.deepcopy(self.doc1)
        forged["continuity"]["events"][-1]["minutes"] = 180
        forged["work_items"][0]["free_work_budget_minutes"] = 180
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(forged, AS_OF, self.receipt0)

        self.authorize(self.doc1)
        os.environ[authority.PROVIDER_ENV] = "rotated-provider"
        with self.assertRaisesRegex(gate.PayoffPathError, "HMAC mismatch"):
            gate.compile_gate(self.doc1, AS_OF, self.receipt0)

    def test_stale_and_future_host_captures_fail_closed(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stale = core._render_timestamp(now - timedelta(seconds=authority.MAX_AUTHORITY_AGE_SECONDS + 2))
        self.authorize(self.doc1, captured_at_utc=stale)
        with self.assertRaisesRegex(gate.PayoffPathError, "stale"):
            gate.compile_gate(self.doc1, AS_OF, self.receipt0)

        future = core._render_timestamp(now + timedelta(seconds=60))
        self.authorize(self.doc1, captured_at_utc=future)
        with self.assertRaisesRegex(gate.PayoffPathError, "future"):
            gate.compile_gate(self.doc1, AS_OF, self.receipt0)

    def test_z_module_reload_cannot_restore_unsigned_public_or_direct_paths(self):
        original = (self.packet1, self.markdown1, self.receipt1)
        self.clear_authority_receipt()
        os.environ["BOUNTY_PAYOFF_POLICY_TEST_ONLY_ALLOW_UNSIGNED"] = "1"

        importlib.reload(v3)

        self.assertEqual("CHAINED", v3.MODE)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.compile_gate(self.doc1, AS_OF, self.receipt0)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            v3._compile_v3(self.doc1, AS_OF, self.receipt0)
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            gate.verify_gate(
                self.doc1,
                self.packet1,
                self.markdown1,
                self.receipt1,
                AS_OF,
                self.receipt0,
            )
        with self.assertRaisesRegex(gate.PayoffPathError, "detached host authority"):
            v3.verify_v3(
                self.doc1,
                self.packet1,
                self.markdown1,
                self.receipt1,
                AS_OF,
                self.receipt0,
            )

        # Valid detached authority still produces byte-identical evidence after reload.
        self.authorize(self.doc1)
        self.assertEqual(original, gate.compile_gate(self.doc1, AS_OF, self.receipt0))
        self.assertEqual(original, v3._compile_v3(self.doc1, AS_OF, self.receipt0))
        self.assertTrue(
            gate.verify_gate(
                self.doc1,
                self.packet1,
                self.markdown1,
                self.receipt1,
                AS_OF,
                self.receipt0,
            )
        )

        # Reload must not turn the stored raw core callable into the dispatcher itself.
        legacy = {"schema": gate.LEGACY_WORK_SCHEMA, "work_items": [work_item(budget=60, spent=0)]}
        legacy_packet, _, _ = gate.compile_legacy_migration_gate(legacy, AS_OF)
        self.assertEqual("LEGACY_REPLAY_ONLY", legacy_packet["continuity"]["mode"])


if __name__ == "__main__":
    unittest.main()
