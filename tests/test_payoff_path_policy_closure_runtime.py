from __future__ import annotations

import copy
import importlib
import types

import pytest

from conftest import _authorize
from concierge import payoff_path_gate as gate
from concierge import payoff_path_policy_authority as authority
from concierge import payoff_path_policy_secure_runtime as secure_runtime
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


def _forge_higher_cap(document):
    forged = copy.deepcopy(document)
    forged["continuity"]["events"][0]["minutes"] = 600
    forged["work_items"][0]["free_work_budget_minutes"] = 600
    return forged


def test_runtime_contract_truthfully_excludes_closure_traversal_metadata_mutation():
    """The exact predecessor attack is not misrepresented as in-scope resistance."""
    secure = v3._compile_v3
    captured_functions = [
        cell.cell_contents
        for cell in (secure.__closure__ or ())
        if isinstance(cell.cell_contents, types.FunctionType)
    ]
    assert any(
        fn.__name__ == "verify_current_policy_authority"
        for fn in captured_functions
    ), "predecessor closure traversal must remain mechanically demonstrable"

    assert secure_runtime.SAME_PROCESS_CLOSURE_INTROSPECTION_RESISTANT is False
    assert secure_runtime.CAPTURED_FUNCTION_METADATA_MUTATION_IN_SCOPE is False
    assert secure_runtime.PUBLIC_BINDING_REPLACEMENT_IN_SCOPE is True
    assert secure_runtime.PUBLIC_ORIGINAL_FUNCTION_METADATA_MUTATION_IN_SCOPE is True
    assert (
        secure_runtime.RECOMMENDED_HIGHER_ASSURANCE_BOUNDARY
        == "CONTROLLED_FRESH_INTERPRETER"
    )
    runtime_doc = secure_runtime.__doc__ or ""
    assert "walk a supported" in runtime_doc
    assert "captured/private function" in runtime_doc
    assert "outside this runtime's threat boundary" in runtime_doc


def test_module_base_setter_and_dict_writes_cannot_replace_runtime_authority(monkeypatch):
    document = _valid_v3_document()
    original_verifier = authority.verify_current_policy_authority
    original_modulus = authority._BOOTSTRAP_MODULUS_TEXT
    original_provider = authority._BOOTSTRAP_PROVIDER_TEXT

    monkeypatch.delenv(authority.CAPTURED_AT_ENV, raising=False)
    monkeypatch.delenv(authority.SIGNATURE_ENV, raising=False)

    try:
        types.ModuleType.__setattr__(
            authority,
            "verify_current_policy_authority",
            lambda document: {"forged": True},
        )
        assert authority.verify_current_policy_authority is not original_verifier

        authority.__dict__["_BOOTSTRAP_MODULUS_TEXT"] = "f" * 512
        authority.__dict__["_BOOTSTRAP_PROVIDER_TEXT"] = "attacker-provider"

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


def test_mutable_public_verifier_kwdefaults_cannot_forge_higher_policy(monkeypatch):
    original = _valid_v3_document()
    _authorize(original, monkeypatch)
    forged = _forge_higher_cap(original)

    public_defaults = authority.verify_current_policy_authority.__kwdefaults__
    assert public_defaults is not None
    monkeypatch.setitem(public_defaults, "_compare_digest", lambda _left, _right: True)

    with pytest.raises(gate.PayoffPathError, match="RSA signature mismatch"):
        gate.compile_gate(forged, AS_OF)


def test_nested_public_verifier_defaults_cannot_retarget_signed_scope(monkeypatch):
    original = _valid_v3_document()
    signed_scope = authority.policy_authority_scope_sha256(original)
    _authorize(original, monkeypatch)
    forged = _forge_higher_cap(original)

    public_defaults = authority.verify_current_policy_authority.__kwdefaults__
    assert public_defaults is not None
    payload_impl = public_defaults["_payload_impl"]
    payload_defaults = payload_impl.__kwdefaults__
    assert payload_defaults is not None

    monkeypatch.setitem(
        payload_defaults,
        "_scope_sha256",
        lambda _document: signed_scope,
    )

    with pytest.raises(gate.PayoffPathError, match="RSA signature mismatch"):
        gate.compile_gate(forged, AS_OF)


def test_v3_global_hook_cannot_mutate_document_between_auth_and_semantics(monkeypatch):
    document = _valid_v3_document()
    pristine = copy.deepcopy(document)
    _authorize(document, monkeypatch)

    expected_packet, expected_markdown, expected_receipt = gate.compile_gate(
        document,
        AS_OF,
    )

    original_hook = v3.__dict__["_verify_current_policy_authority"]
    hostile_calls = []

    def mutate_after_outer_verify(candidate):
        hostile_calls.append(True)
        candidate["continuity"]["events"][0]["minutes"] = 600
        candidate["work_items"][0]["free_work_budget_minutes"] = 600
        return {"forged": True}

    try:
        v3.__dict__["_verify_current_policy_authority"] = mutate_after_outer_verify
        _authorize(document, monkeypatch)

        public_packet, public_markdown, public_receipt = gate.compile_gate(
            document,
            AS_OF,
        )
        direct_packet, direct_markdown, direct_receipt = v3._compile_v3(
            document,
            AS_OF,
            None,
        )

        assert not hostile_calls
        assert document == pristine
        assert public_packet == expected_packet == direct_packet
        assert public_markdown == expected_markdown == direct_markdown
        assert public_receipt == expected_receipt == direct_receipt
        assert public_packet["continuity"]["policy_heads"][0]["cap_minutes"] == 60

        assert gate.verify_gate(
            document,
            expected_packet,
            expected_markdown,
            expected_receipt,
            AS_OF,
        )
        assert v3.verify_v3(
            document,
            expected_packet,
            expected_markdown,
            expected_receipt,
            trusted_now=AS_OF,
            previous_receipt=None,
        )
        assert not hostile_calls
        assert document == pristine
    finally:
        v3.__dict__["_verify_current_policy_authority"] = original_hook
