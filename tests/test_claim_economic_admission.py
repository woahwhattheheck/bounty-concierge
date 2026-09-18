# SPDX-License-Identifier: MIT
"""Paid-work economics must be replayed from retained request bytes before claim instructions."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from concierge import claim_economic_admission as gate
from concierge.paid_work_effort_value_gate import compile_paid_work_effort_value_gate


POLICY = {
    "schema": "paid-work-effort-value-policy/v1",
    "evidence_max_age_seconds": 86400,
    "deadline_safety_seconds": 3600,
    "max_active_claims": 1,
    "fleet_economic_policy": {
        "schema": "fleet-economic-policy/v1",
        "min_batch_items": 2,
        "max_batch_items": 200,
        "currencies": {
            "USD": {
                "min_single_reward": "100",
                "min_batch_reward": "500",
                "min_reward_per_agent_hour": "100",
            },
            "RTC": {
                "min_single_reward": "200",
                "min_batch_reward": "500",
                "min_reward_per_agent_hour": "300",
            },
        },
    },
}


def _evidence(state, *, authority=None):
    value = {
        "state": state,
        "evidence_url": "https://evidence.example/proof",
        "observed_at": "2026-09-17T19:30:00Z",
    }
    if authority is not None:
        value["authority"] = authority
    return value


def _request(
    *,
    work_id="work-42",
    source="https://github.com/acme/widget/issues/42",
    amount="300",
    currency="USD",
    unit_type="CASH",
):
    return {
        "schema": "paid-work-effort-value-gate/v1",
        "as_of": "2026-09-17T20:00:00Z",
        "policy": deepcopy(POLICY),
        "candidate": {
            "work_id": work_id,
            "canonical_source_url": source,
            "advertised_payout": {
                "amount": amount,
                "currency": currency,
                "unit_type": unit_type,
                "observed_at": "2026-09-17T19:30:00Z",
            },
            "estimated_engineering_hours": "1",
            "model_tool_cost": {
                "state": "KNOWN",
                "amount": "10" if currency == "USD" else "0",
                "currency": currency,
            },
            "deadline_at": "2026-09-18T20:00:00Z",
            "congestion": {
                "active_claims": 0,
                "observed_at": "2026-09-17T19:30:00Z",
            },
            "acceptance": _evidence("CONFIRMED", authority="FIRST_PARTY"),
            "payout_route": _evidence("CONFIRMED"),
            "account_kyc": _evidence("READY"),
        },
    }


def _payoff(work_id="work-42"):
    return {
        "schema": "payoff-claim-proof/v2",
        "repo": "acme/widget",
        "issue": 42,
        "canonical_issue_url": "https://github.com/acme/widget/issues/42",
        "work_id": work_id,
    }


def _write(path: Path, value: dict, raw: bytes | None = None) -> bytes:
    payload = raw if raw is not None else json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(payload)
    return payload


def _verify(tmp_path: Path, request: dict, receipt: dict | None = None, **overrides):
    receipt = compile_paid_work_effort_value_gate(request) if receipt is None else receipt
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    _write(request_path, request)
    _write(receipt_path, receipt)
    kwargs = {"decision_as_of": "2026-09-17T20:30:00Z"}
    kwargs.update(overrides)
    return gate.verify_claim_economic_receipt(
        "acme/widget",
        42,
        _payoff(),
        request_path,
        receipt_path,
        **kwargs,
    )


def _rehash_receipt(receipt: dict) -> None:
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_live_verifier_generation_ignores_public_rebinding_and_authority_mutation(
    monkeypatch, tmp_path
):
    request = _request(amount="14", currency="RTC", unit_type="NONCASH")
    receipt = compile_paid_work_effort_value_gate(request)
    assert receipt["decision"] == "HOLD_VALUE_UNKNOWN"
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    _write(request_path, request)
    _write(receipt_path, receipt)

    original_error = gate.ClaimEconomicAdmissionError
    forged = deepcopy(receipt)
    forged["decision"] = "GO"
    forged["reason_codes"] = ["ALL_GATES_CLEAR"]
    _rehash_receipt(forged)

    monkeypatch.setattr(gate, "compile_paid_work_effort_value_gate", lambda _request: forged)
    monkeypatch.setattr(gate, "verify_receipt", lambda _receipt: True)
    monkeypatch.setattr(
        gate,
        "_payoff_context",
        lambda *_args: ("work-42", "https://github.com/acme/widget/issues/42"),
    )
    monkeypatch.setattr(
        gate,
        "_exact_utc",
        lambda *_args: ("2026-09-17T20:30:00Z", object()),
    )
    monkeypatch.setattr(gate, "_max_age", lambda _value: 86400)
    monkeypatch.setattr(gate, "_read_bounded_regular", lambda _path: b"{}")
    monkeypatch.setattr(gate, "_strict_json", lambda _payload: forged)
    monkeypatch.setattr(gate, "_sha256", lambda *_args: "0" * 64)
    monkeypatch.setattr(gate, "_canonical_sha256", lambda _value: "0" * 64)
    monkeypatch.setattr(gate, "_EXPECTED_POLICY_SHA256", "0" * 64)
    monkeypatch.setattr(gate, "_SUPPORTED_DECISIONS", frozenset({"GO"}))
    monkeypatch.setitem(gate._EXPECTED_AUTHORITY, "external_claim_authority", True)
    monkeypatch.setattr(gate, "ClaimEconomicAdmissionError", RuntimeError)
    monkeypatch.setattr(gate, "PROOF_SCHEMA", "poisoned-proof/v999")

    with pytest.raises(original_error) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            _payoff(),
            request_path,
            receipt_path,
            decision_as_of="2026-09-17T20:30:00Z",
        )

    assert caught.value.code == "ECONOMICS_HOLD_VALUE_UNKNOWN"


def test_source_owned_policy_digest_matches_checked_in_policy():
    checked_in = json.loads(
        Path("policies/paid_work_effort_value_v1.json").read_text(encoding="utf-8")
    )
    canonical = json.dumps(
        checked_in,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == gate._EXPECTED_POLICY_SHA256


def test_verified_go_replays_request_and_binds_target(tmp_path):
    request = _request()
    proof = _verify(tmp_path, request)
    assert proof["schema"] == "claim-economic-admission-proof/v2"
    assert proof["decision"] == "GO"
    assert proof["work_id"] == "work-42"
    assert proof["canonical_issue_url"] == "https://github.com/acme/widget/issues/42"
    assert proof["age_seconds"] == 1800
    assert proof["verified"] is True
    assert proof["authority"] == "INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION"
    assert len(proof["gate_request_bytes_sha256"]) == 64
    assert len(proof["gate_receipt_bytes_sha256"]) == 64
    assert len(proof["binding_sha256"]) == 64


def test_noncash_unknown_value_cannot_emit_claim_instructions(tmp_path):
    request = _request(amount="14", currency="RTC", unit_type="NONCASH")
    receipt = compile_paid_work_effort_value_gate(request)
    assert receipt["decision"] == "HOLD_VALUE_UNKNOWN"
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, request, receipt)
    assert caught.value.code == "ECONOMICS_HOLD_VALUE_UNKNOWN"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("work_id", "other-work", "ECONOMIC_WORK_MISMATCH"),
        ("source", "https://github.com/acme/other/issues/42", "ECONOMIC_SOURCE_MISMATCH"),
    ],
)
def test_replayed_work_and_source_mismatch_fail_closed(tmp_path, field, value, code):
    request = _request(**{field: value})
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, request)
    assert caught.value.code == code


def test_rehashed_forged_go_cannot_override_replayed_hold(tmp_path):
    request = _request(amount="14", currency="RTC", unit_type="NONCASH")
    forged = compile_paid_work_effort_value_gate(request)
    forged["decision"] = "GO"
    forged["reason_codes"] = ["ALL_GATES_CLEAR"]
    _rehash_receipt(forged)
    assert gate.verify_receipt(forged) is True
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, request, forged)
    assert caught.value.code == "ECONOMIC_RECEIPT_REPLAY_MISMATCH"


def test_weak_caller_policy_cannot_mint_live_admission(tmp_path):
    request = _request(amount="14", currency="RTC")
    request["policy"]["fleet_economic_policy"]["currencies"]["RTC"]["min_single_reward"] = "1"
    request["policy"]["fleet_economic_policy"]["currencies"]["RTC"]["min_reward_per_agent_hour"] = "1"
    receipt = compile_paid_work_effort_value_gate(request)
    assert receipt["decision"] == "GO"
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, request, receipt)
    assert caught.value.code == "ECONOMIC_POLICY_MISMATCH"


@pytest.mark.parametrize(
    "decision_as_of,code",
    [
        ("2026-09-17T19:59:59Z", "ECONOMIC_RECEIPT_FUTURE"),
        ("2026-09-17T21:00:01Z", "ECONOMIC_RECEIPT_STALE"),
    ],
)
def test_decision_time_enforces_future_and_stale_boundaries(tmp_path, decision_as_of, code):
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, _request(), decision_as_of=decision_as_of)
    assert caught.value.code == code


def test_payoff_proof_identity_is_mandatory(tmp_path):
    request = _request()
    receipt = compile_paid_work_effort_value_gate(request)
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    _write(request_path, request)
    _write(receipt_path, receipt)
    bad = _payoff()
    bad["issue"] = 43
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            bad,
            request_path,
            receipt_path,
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_PAYOFF_BINDING"


@pytest.mark.parametrize("artifact", ["request", "receipt"])
def test_duplicate_json_key_is_rejected_before_semantic_replay(tmp_path, artifact):
    request = _request()
    receipt = compile_paid_work_effort_value_gate(request)
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    request_raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    receipt_raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    if artifact == "request":
        request_raw = request_raw[:-1] + b',"schema":"paid-work-effort-value-gate/v1"}'
    else:
        receipt_raw = receipt_raw[:-1] + b',"decision":"GO"}'
    _write(request_path, request, raw=request_raw)
    _write(receipt_path, receipt, raw=receipt_raw)
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget", 42, _payoff(), request_path, receipt_path,
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"


@pytest.mark.parametrize("artifact", ["request", "receipt"])
def test_huge_integer_token_is_typed_fail_closed(tmp_path, artifact):
    request = _request()
    receipt = compile_paid_work_effort_value_gate(request)
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    _write(request_path, request)
    _write(receipt_path, receipt)
    target = request_path if artifact == "request" else receipt_path
    target.write_bytes(b'{"x":' + (b"9" * 5000) + b"}")
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget", 42, _payoff(), request_path, receipt_path,
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"


@pytest.mark.parametrize("artifact", ["request", "receipt"])
def test_symlinked_economic_artifact_is_rejected(tmp_path, artifact):
    request = _request()
    receipt = compile_paid_work_effort_value_gate(request)
    request_path = tmp_path / "economic-request.json"
    receipt_path = tmp_path / "economic-receipt.json"
    _write(request_path, request)
    _write(receipt_path, receipt)
    original = request_path if artifact == "request" else receipt_path
    link = tmp_path / f"linked-{artifact}.json"
    try:
        link.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    if artifact == "request":
        request_path = link
    else:
        receipt_path = link
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget", 42, _payoff(), request_path, receipt_path,
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"
