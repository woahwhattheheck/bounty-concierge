# SPDX-License-Identifier: MIT
"""Exact paid-work economics must bind one payoff-proven live claim target."""

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


def _write(tmp_path: Path, receipt: dict, *, raw: bytes | None = None):
    payload = raw if raw is not None else json.dumps(
        receipt, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path = tmp_path / "economic-receipt.json"
    path.write_bytes(payload)
    return path, payload, hashlib.sha256(payload).hexdigest()


def _verify(tmp_path: Path, receipt: dict, **overrides):
    path, payload, digest = _write(tmp_path, receipt)
    kwargs = {
        "expected_receipt_bytes_sha256": digest,
        "expected_policy_sha256": receipt["policy_sha256"],
        "decision_as_of": "2026-09-17T20:30:00Z",
    }
    kwargs.update(overrides)
    return gate.verify_claim_economic_receipt(
        "acme/widget",
        42,
        _payoff(),
        path,
        **kwargs,
    )


def test_verified_go_binds_payoff_work_target_and_exact_bytes(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    proof = _verify(tmp_path, receipt)

    assert proof["schema"] == "claim-economic-admission-proof/v1"
    assert proof["decision"] == "GO"
    assert proof["work_id"] == "work-42"
    assert proof["canonical_issue_url"] == "https://github.com/acme/widget/issues/42"
    assert proof["age_seconds"] == 1800
    assert proof["verified"] is True
    assert proof["authority"] == "INTERNAL_CLAIM_INSTRUCTION_ADMISSION_ONLY_NO_EXTERNAL_ACTION"
    assert len(proof["binding_sha256"]) == 64


def test_noncash_unknown_value_cannot_emit_claim_instructions(tmp_path):
    receipt = compile_paid_work_effort_value_gate(
        _request(amount="14", currency="RTC", unit_type="NONCASH")
    )
    assert receipt["decision"] == "HOLD_VALUE_UNKNOWN"
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, receipt)
    assert caught.value.code == "ECONOMICS_HOLD_VALUE_UNKNOWN"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("work_id", "other-work", "ECONOMIC_WORK_MISMATCH"),
        (
            "source",
            "https://github.com/acme/other/issues/42",
            "ECONOMIC_SOURCE_MISMATCH",
        ),
    ],
)
def test_work_and_source_mismatch_fail_closed(tmp_path, field, value, code):
    receipt = compile_paid_work_effort_value_gate(_request(**{field: value}))
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, receipt)
    assert caught.value.code == code


def test_exact_bytes_and_policy_are_independently_bound(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    path, payload, digest = _write(tmp_path, receipt)

    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            _payoff(),
            path,
            expected_receipt_bytes_sha256="0" * 64,
            expected_policy_sha256=receipt["policy_sha256"],
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "ECONOMIC_RECEIPT_BYTES_MISMATCH"

    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            _payoff(),
            path,
            expected_receipt_bytes_sha256=digest,
            expected_policy_sha256="0" * 64,
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "ECONOMIC_POLICY_MISMATCH"


@pytest.mark.parametrize(
    "decision_as_of,code",
    [
        ("2026-09-17T19:59:59Z", "ECONOMIC_RECEIPT_FUTURE"),
        ("2026-09-17T21:00:01Z", "ECONOMIC_RECEIPT_STALE"),
    ],
)
def test_explicit_decision_time_enforces_future_and_stale_boundaries(
    tmp_path, decision_as_of, code
):
    receipt = compile_paid_work_effort_value_gate(_request())
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, receipt, decision_as_of=decision_as_of)
    assert caught.value.code == code


def test_payoff_proof_identity_is_mandatory(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    path, payload, digest = _write(tmp_path, receipt)
    bad = _payoff()
    bad["issue"] = 43
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            bad,
            path,
            expected_receipt_bytes_sha256=digest,
            expected_policy_sha256=receipt["policy_sha256"],
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_PAYOFF_BINDING"


def test_duplicate_json_key_is_rejected_before_semantic_verification(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw = raw[:-1] + b',"decision":"GO"}'
    path, payload, digest = _write(tmp_path, receipt, raw=raw)
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            _payoff(),
            path,
            expected_receipt_bytes_sha256=digest,
            expected_policy_sha256=receipt["policy_sha256"],
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"


def test_authority_amplification_fails_even_when_outer_receipt_is_rehashed(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    changed = deepcopy(receipt)
    changed["authority"]["external_claim_authority"] = True
    body = dict(changed)
    body.pop("receipt_sha256")
    changed["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        _verify(tmp_path, changed)
    assert caught.value.code == "INVALID_ECONOMIC_AUTHORITY"


def test_symlink_receipt_path_is_rejected(tmp_path):
    receipt = compile_paid_work_effort_value_gate(_request())
    target, payload, digest = _write(tmp_path, receipt)
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        gate.verify_claim_economic_receipt(
            "acme/widget",
            42,
            _payoff(),
            link,
            expected_receipt_bytes_sha256=digest,
            expected_policy_sha256=receipt["policy_sha256"],
            decision_as_of="2026-09-17T20:30:00Z",
        )
    assert caught.value.code == "INVALID_ECONOMIC_RECEIPT"
