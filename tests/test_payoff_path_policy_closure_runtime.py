from __future__ import annotations

import importlib
import types

import pytest

from concierge import payoff_path_gate as gate
from concierge import payoff_path_policy_authority as authority
from concierge import payoff_path_policy_v3 as v3

AS_OF = "2026-09-13T15:00:00.000Z"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _valid_v3_document():
    return {
        "schema": gate.WORK_SCHEMA_V3,
        "continuity": {
            "schema": gate.CONTINUITY_SCHEMA_V2,
            "ledger_id": "closure-runtime-ledger",
            "generation": 0,
            "previous_receipt_sha256": None,
            "events": [
                {
                    "event_id": "policy-0",
                    "kind": "BUDGET_POLICY",
                    "opportunity_id": "opp-closure",
                    "work_id": None,
                    "minutes": 60,
                    "occurred_at_utc": "2026-09-13T12:30:00.000Z",
                    "evidence_ref": "owner-policy:closure-0",
                    "evidence_sha256": SHA_B,
                    "policy_generation": 0,
                    "predecessor_policy_sha256": None,
                }
            ],
        },
        "work_items": [
            {
                "work_id": "work-closure",
                "opportunity_id": "opp-closure",
                "started_at_utc": "2026-09-13T12:30:00.000Z",
                "free_work_budget_minutes": 60,
                "free_work_spent_minutes": 0,
                "payoff_path": {
                    "mechanism": "BOUNTY",
                    "value": {
                        "kind": "FIXED",
                        "currency": "USD",
                        "amount_minor": 9000,
                    },
                    "source": {
                        "canonical_url": "https://example.com/opportunity/closure",
                        "evidence_ref": "source:closure",
                        "evidence_sha256": SHA_A,
                        "observed_at_utc": "2026-09-13T12:00:00.000Z",
                        "max_age_days": 7,
                    },
                    "conversion": {
                        "event": "SUBMIT_WORK",
                        "due_at_utc": "2026-09-20T12:00:00.000Z",
                        "evidence_ref": "conversion:closure",
                        "evidence_sha256": SHA_B,
                    },
                },
            }
        ],
    }


def test_module_base_setter_and_dict_writes_cannot_replace_runtime_authority(monkeypatch):
    document = _valid_v3_document()
    original_verifier = authority.verify_current_policy_authority
    original_modulus = authority._BOOTSTRAP_MODULUS_TEXT
    original_provider = authority._BOOTSTRAP_PROVIDER_TEXT

    monkeypatch.delenv(authority.CAPTURED_AT_ENV, raising=False)
    monkeypatch.delenv(authority.SIGNATURE_ENV, raising=False)

    try:
        # Bypass the authority module subclass exactly as the predecessor review did.
        types.ModuleType.__setattr__(
            authority,
            "verify_current_policy_authority",
            lambda document: {"forged": True},
        )
        assert authority.verify_current_policy_authority is not original_verifier

        # Direct module-dict writes bypass __setattr__ as well. Neither can affect the
        # closure-held verifier nor its captured first-bootstrap identity.
        authority.__dict__["_BOOTSTRAP_MODULUS_TEXT"] = "f" * 512
        authority.__dict__["_BOOTSTRAP_PROVIDER_TEXT"] = "attacker-provider"

        # Ordinary reload is a fail-safe no-op after trusted bootstrap. It must not
        # reconstruct raw v3 functions that reacquire the attacker module attribute.
        assert importlib.reload(v3) is v3
        assert v3.MODE == "CHAINED"

        calls = (
            lambda: gate.compile_gate(document, AS_OF),
            lambda: v3._compile_v3(document, AS_OF, None),
        )
        for call in calls:
            with pytest.raises(gate.PayoffPathError, match="authority configuration is incomplete"):
                call()
    finally:
        # Restore process-global test state with the same primitive used by the hostile.
        types.ModuleType.__setattr__(
            authority,
            "verify_current_policy_authority",
            original_verifier,
        )
        authority.__dict__["_BOOTSTRAP_MODULUS_TEXT"] = original_modulus
        authority.__dict__["_BOOTSTRAP_PROVIDER_TEXT"] = original_provider


def test_module_base_setter_cannot_bypass_public_or_direct_verify(monkeypatch):
    document = _valid_v3_document()
    original_verifier = authority.verify_current_policy_authority
    monkeypatch.delenv(authority.CAPTURED_AT_ENV, raising=False)
    monkeypatch.delenv(authority.SIGNATURE_ENV, raising=False)

    # Packet fields are irrelevant: closure-held authority must reject before semantic
    # verification reaches attacker-controlled packet data.
    forged_packet = {}
    try:
        types.ModuleType.__setattr__(
            authority,
            "verify_current_policy_authority",
            lambda document: {"forged": True},
        )
        calls = (
            lambda: gate.verify_gate(document, forged_packet, "", {}, AS_OF),
            lambda: v3.verify_v3(document, forged_packet, "", {}, AS_OF, None),
        )
        for call in calls:
            with pytest.raises(gate.PayoffPathError, match="authority configuration is incomplete"):
                call()
    finally:
        types.ModuleType.__setattr__(
            authority,
            "verify_current_policy_authority",
            original_verifier,
        )
