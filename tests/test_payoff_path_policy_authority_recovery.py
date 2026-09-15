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

# Test-only trusted RSA-2048 key. Production code receives only N/e at bootstrap.
TEST_RSA_N_HEX = (
    "aff7a78a9d3f1d170b0f27c775a6b5b776bb6eb7f09edbecebfbd12f19e53271"
    "3499c28a82c50cd048a7a5c58a8facacedaa21f8c2763beb07a63032a191e451"
    "5cf0b123d0ec576f9e990d693def17f8075cfa8ee87875eaef16371efef7da06"
    "b5462ae94ea4397308cce0c5c2b965a72566e970984a3ce08060b1f31e64fed0"
    "5751a91ff2eb16696e535f38723bae847cf8398bec3619951232dde3d7de822a5"
    "21a45c885042dfa2f16d82825ffa4798e1cd496b46a0a071ca4fa18b1095c458"
    "8ce392f9d8d2d61ffc5a76d54d91b3e664ef817e9e917c8a8149d05174950653"
    "a62b398cb9ed48115fdf0d4c9a9d66212837687896a2fbf5a66f8464ac7d113"
)
TEST_RSA_D_HEX = (
    "bf6a81dba1fceb2d608a61409d6acbcc2c6197a9dfc5ef2c0ff32334e97bb91e"
    "0c3f76b02b05cf4fd8b905a042d4d231b56c4252d2b6c0572feaf2b876540d84"
    "5f1034a1916886c9ea419e4fc2b4e3e3459f39b005e783de1c56937f7ee67e9f"
    "ed7121721f1490a9e2037c3ef94e53eb39747406574dea6061f0cda279ddc33b6"
    "f4f3ee07e2fb487519a73ab6ee02c444ef9198924b119bc85e0e652692abfe83"
    "802492939402082577fcef7523224084e5f84e4aa316e4ccea3aaabc8d1d13bd9"
    "1bb1bd89eaf3465cca9cd5fe033850d6379384d7ed0f7fba15d3e44d033c19c9"
    "dfa49ee10636e126372702f5ff0f2b6c2a743eac47d863f7750ef5b65e881"
)
TEST_RSA_E = 65537
TEST_PROVIDER = "fixture-provider"
TEST_PRINCIPAL = "9" * 64

# Independent attacker key: changing process environment to this key after bootstrap
# must not create a new trust root.
ATTACKER_RSA_N_HEX = (
    "de7edf561391445a0c8afe49fa2b2eec24371aa9f271bc30a9cf39dea7c1eea0"
    "7a92a9c0e502ed9cc52c74a12549d3e0cfd62f5236b64105a51669f2958fcf5f"
    "ce2ea6be459a7752c96e0b287618f270e3ffdbff0fcf085d64ac3e057524af8a"
    "f7b9f6adcdde3d3971c89d9e96cd230b18a3e98cc37f57191b85f7ba0123f15"
    "c87b3fbbc69765fea939131e91628f62916b5bd804b9fa2548790b1d8317b712"
    "27a07bcdb5bcdc1717df5be93f7b3ef3641949725682da140438d48086f681457"
    "9ec0da1761ddbc1ed97fb9d0f5b008152da825009db1edd279a93860f30210fb0"
    "b09a475de3d1bc014ab228c7256292bf025ecfbf723f8420ec776f48f365f65"
)
ATTACKER_RSA_D_HEX = (
    "8f6d01d349b1d9cf4d4946e5b145526e19ba5be489cbbf75d9ebcd1b8c26265e"
    "d4cf8e6b863553aee77be22daf0d346ee0b640084e09ebf3139a009a1c1ccef5"
    "576d6dece1e427b4a4f1dc722cb9a584c30a6b155f6a2508ef48b3d7c377eea1"
    "3552c4d8580941e107499231bfcc6521560a491785e94f35798496a36a461d38f"
    "0c1e238adb0e4ed0fcb115df4a7d98836578aefb516800e0e65f0904c413f827"
    "4deb4304bea5ce91c9efd904242bf0dfa985c68def9fbf0280aaca3b66c4c9efe"
    "24f3006731b25f97ffcba1eb2088555981ab87703dec1af45d77d1fa5af67470f"
    "a35845962b5622b69467055f882d7b85c7d37a730372f9de0e07871a5c29"
)
DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def rsa_sign_payload(payload, *, modulus_hex=TEST_RSA_N_HEX, private_d_hex=TEST_RSA_D_HEX):
    modulus = int(modulus_hex, 16)
    private_exponent = int(private_d_hex, 16)
    key_bytes = (modulus.bit_length() + 7) // 8
    digest_bytes = hashlib.sha256(canonical_bytes(payload)).digest()
    digest_info = DIGESTINFO_PREFIX + digest_bytes
    padding = b"\xff" * (key_bytes - len(digest_info) - 3)
    encoded = b"\x00\x01" + padding + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), private_exponent, modulus)
    return signature.to_bytes(key_bytes, "big").hex()


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
                authority.MODULUS_ENV: TEST_RSA_N_HEX,
                authority.EXPONENT_ENV: str(TEST_RSA_E),
                authority.PROVIDER_ENV: TEST_PROVIDER,
                authority.PRINCIPAL_ENV: TEST_PRINCIPAL,
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.assertEqual(TEST_RSA_N_HEX, authority._BOOTSTRAP_MODULUS_TEXT)
        self.assertEqual(str(TEST_RSA_E), authority._BOOTSTRAP_EXPONENT_TEXT)
        self.assertEqual(TEST_PROVIDER, authority._BOOTSTRAP_PROVIDER_TEXT)
        self.assertEqual(TEST_PRINCIPAL, authority._BOOTSTRAP_PRINCIPAL_TEXT)

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
        if captured_at_utc is None:
            captured_at_utc = core._render_timestamp(
                datetime.now(timezone.utc).replace(microsecond=0)
            )
        payload = {
            "purpose": authority.AUTHORITY_PURPOSE,
            "provider": TEST_PROVIDER,
            "principal_sha256": TEST_PRINCIPAL,
            "captured_at_utc": captured_at_utc,
            "policy_scope_sha256": authority.policy_authority_scope_sha256(document_value),
        }
        signed = {
            authority.CAPTURED_AT_ENV: captured_at_utc,
            authority.SIGNATURE_ENV: rsa_sign_payload(payload),
        }
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

    def test_production_authority_is_verify_only(self):
        self.assertFalse(hasattr(authority, "_sign_current_policy_authority_for_host_fixture"))
        self.assertFalse(hasattr(authority, "PRIVATE_KEY_ENV"))
        self.assertFalse(hasattr(authority, "KEY_ENV"))

    def test_policy_mutation_and_attacker_key_substitution_fail_closed(self):
        forged = copy.deepcopy(self.doc1)
        forged["continuity"]["events"][-1]["minutes"] = 180
        forged["work_items"][0]["free_work_budget_minutes"] = 180
        with self.assertRaisesRegex(gate.PayoffPathError, "RSA signature mismatch"):
            gate.compile_gate(forged, AS_OF, self.receipt0)

        # Post-bootstrap identity/key environment mutation cannot replace the retained
        # trust anchor: a still-valid original signature remains valid.
        self.authorize(self.doc1)
        original = (self.packet1, self.markdown1, self.receipt1)
        os.environ[authority.MODULUS_ENV] = ATTACKER_RSA_N_HEX
        os.environ[authority.EXPONENT_ENV] = str(TEST_RSA_E)
        os.environ[authority.PROVIDER_ENV] = "attacker-provider"
        os.environ[authority.PRINCIPAL_ENV] = "8" * 64
        self.assertEqual(original, gate.compile_gate(self.doc1, AS_OF, self.receipt0))

        # Nor can the attacker self-sign a forged policy with that substituted key.
        captured = core._render_timestamp(datetime.now(timezone.utc).replace(microsecond=0))
        attacker_payload = {
            "purpose": authority.AUTHORITY_PURPOSE,
            "provider": "attacker-provider",
            "principal_sha256": "8" * 64,
            "captured_at_utc": captured,
            "policy_scope_sha256": authority.policy_authority_scope_sha256(forged),
        }
        os.environ[authority.CAPTURED_AT_ENV] = captured
        os.environ[authority.SIGNATURE_ENV] = rsa_sign_payload(
            attacker_payload,
            modulus_hex=ATTACKER_RSA_N_HEX,
            private_d_hex=ATTACKER_RSA_D_HEX,
        )
        with self.assertRaisesRegex(gate.PayoffPathError, "RSA signature mismatch"):
            gate.compile_gate(forged, AS_OF, self.receipt0)

        # Ordinary reload also preserves first-bootstrap identity, and direct attribute
        # replacement of the retained private anchor is ignored.
        importlib.reload(authority)
        self.assertEqual(TEST_RSA_N_HEX, authority._BOOTSTRAP_MODULUS_TEXT)
        authority._BOOTSTRAP_MODULUS_TEXT = ATTACKER_RSA_N_HEX
        self.assertEqual(TEST_RSA_N_HEX, authority._BOOTSTRAP_MODULUS_TEXT)

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

    def test_z_public_verifier_rebinding_and_reload_cannot_bypass_authority(self):
        original = (self.packet1, self.markdown1, self.receipt1)
        original_verifier = authority.verify_current_policy_authority

        authority.verify_current_policy_authority = lambda document: {"forged": True}
        self.assertIs(original_verifier, authority.verify_current_policy_authority)
        with self.assertRaises(AttributeError):
            del authority.verify_current_policy_authority

        self.clear_authority_receipt()
        os.environ["BOUNTY_PAYOFF_POLICY_TEST_ONLY_ALLOW_UNSIGNED"] = "1"
        importlib.reload(v3)

        self.assertEqual("CHAINED", v3.MODE)
        for compile_call in (
            lambda: gate.compile_gate(self.doc1, AS_OF, self.receipt0),
            lambda: v3._compile_v3(self.doc1, AS_OF, self.receipt0),
        ):
            with self.assertRaisesRegex(gate.PayoffPathError, "authority configuration is incomplete"):
                compile_call()
        for verify_call in (
            lambda: gate.verify_gate(
                self.doc1, self.packet1, self.markdown1, self.receipt1, AS_OF, self.receipt0
            ),
            lambda: v3.verify_v3(
                self.doc1, self.packet1, self.markdown1, self.receipt1, AS_OF, self.receipt0
            ),
        ):
            with self.assertRaisesRegex(gate.PayoffPathError, "authority configuration is incomplete"):
                verify_call()

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

        legacy = {"schema": gate.LEGACY_WORK_SCHEMA, "work_items": [work_item(budget=60, spent=0)]}
        legacy_packet, _, _ = gate.compile_legacy_migration_gate(legacy, AS_OF)
        self.assertEqual("LEGACY_REPLAY_ONLY", legacy_packet["continuity"]["mode"])


if __name__ == "__main__":
    unittest.main()
