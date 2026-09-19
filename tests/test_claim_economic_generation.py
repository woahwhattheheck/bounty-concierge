# SPDX-License-Identifier: MIT
"""Regression coverage for PR #238's transitive economic replay generation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import runpy
import shutil
import sys
import types

import pytest

from concierge import claim_economic_admission as gate
from concierge import paid_work_effort_value_gate as pwev
from concierge import fleet_economic_admission as fleet
from concierge import _fleet_economic_admission_v1 as base

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-09-17T20:30:00Z"
SOURCE_FILES = (
    "_fleet_economic_admission_v1.py", "fleet_economic_admission.py",
    "paid_work_effort_value_gate.py", "_claim_economic_admission_impl.py",
)


def request(amount="300", hours="1", unit_type="CASH"):
    def evidence(state, **extra):
        return dict(state=state, evidence_url="https://evidence.example/proof",
                    observed_at="2026-09-17T19:30:00Z", **extra)
    return {
        "schema": "paid-work-effort-value-gate/v1",
        "as_of": "2026-09-17T20:00:00Z",
        "policy": json.loads((ROOT / "policies/paid_work_effort_value_v1.json").read_text()),
        "candidate": {
            "work_id": "work-42",
            "canonical_source_url": "https://github.com/acme/widget/issues/42",
            "advertised_payout": {
                "amount": amount, "currency": "USD", "unit_type": unit_type,
                "observed_at": "2026-09-17T19:30:00Z",
            },
            "estimated_engineering_hours": hours,
            "model_tool_cost": {"state": "KNOWN", "amount": "10", "currency": "USD"},
            "deadline_at": "2026-09-18T20:00:00Z",
            "congestion": {"active_claims": 0, "observed_at": "2026-09-17T19:30:00Z"},
            "acceptance": evidence("CONFIRMED", authority="FIRST_PARTY"),
            "payout_route": evidence("CONFIRMED"),
            "account_kyc": evidence("READY"),
        },
    }


def payoff():
    return dict(schema="payoff-claim-proof/v2", repo="acme/widget", issue=42,
                canonical_issue_url="https://github.com/acme/widget/issues/42",
                work_id="work-42")


def retain(tmp_path, req, receipt):
    request_path, receipt_path = tmp_path / "request.json", tmp_path / "receipt.json"
    for path, value in ((request_path, req), (receipt_path, receipt)):
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return request_path, receipt_path


def verify(paths):
    return gate.verify_claim_economic_receipt(
        "acme/widget", 42, payoff(), *paths, decision_as_of=AS_OF,
    )


def test_below_hourly_floor_cannot_be_reminted_by_public_compiler(monkeypatch, tmp_path):
    req = request(amount="110", hours="1.01")
    authentic = pwev.compile_paid_work_effort_value_gate(req)
    assert authentic["decision"] == "SKIP_ECONOMICS"
    assert "POST_COST_REWARD_RATE_FLOOR_NOT_MET" in authentic["reason_codes"]
    assert authentic["economics"]["net_reward_after_model_tool_cost"] == "100"
    monkeypatch.setattr(pwev, "_currency_floor", lambda *_: (Decimal(0), Decimal(0)))
    forged = pwev.compile_paid_work_effort_value_gate(req)
    assert forged["decision"] == "GO"  # Proves the original reported mutation is active.
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        verify(retain(tmp_path, req, forged))
    assert caught.value.code == "ECONOMIC_RECEIPT_REPLAY_MISMATCH"
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        verify(retain(tmp_path, req, authentic))
    assert caught.value.code == "ECONOMICS_SKIP_ECONOMICS"


@pytest.mark.parametrize("module,name,replacement", [
    (pwev, "_currency_floor", lambda *_: (Decimal(0), Decimal(0))),
    (pwev, "_sha256_json", lambda *_: "0" * 64),
    (pwev, "_strict_json_bytes", lambda *_: {}),
    (pwev, "verify_receipt", lambda *_: False),
    (pwev, "Decimal", None),
    (pwev, "localcontext", None),
    (pwev, "json", None),
    (pwev, "hashlib", None),
    (pwev, "re", None),
    (pwev, "_RECEIPT_SCHEMA", "changed"),
    (pwev, "_ALLOWED_DECISIONS", frozenset()),
    (fleet, "compile_fleet_economic_admission", lambda *_: {}),
    (fleet, "verify_receipt", lambda *_: False),
    (fleet, "_source_identity", lambda *_: "changed"),
    (base, "compile_fleet_economic_admission", lambda *_: {}),
    (base, "verify_receipt", lambda *_: False),
    (base, "_sha256_json", lambda *_: "0" * 64),
])
def test_public_transitive_rebinding_does_not_change_valid_proof(
    monkeypatch, tmp_path, module, name, replacement
):
    req = request()
    paths = retain(tmp_path, req, pwev.compile_paid_work_effort_value_gate(req))
    expected = verify(paths)
    monkeypatch.setattr(module, name, replacement)
    assert verify(paths) == expected


def test_captured_helper_default_mutations_do_not_change_proof(monkeypatch, tmp_path):
    req = request()
    paths = retain(tmp_path, req, pwev.compile_paid_work_effort_value_gate(req))
    expected = verify(paths)
    # These are the EXACT exported-helper surfaces named by the prior review.
    monkeypatch.setitem(gate._strict_json.__kwdefaults__, "_json_loads", lambda *_a, **_k: {})
    monkeypatch.setitem(gate._strict_json.__kwdefaults__, "_max_bytes", 1)
    monkeypatch.setitem(gate._parse_int.__kwdefaults__, "_max_safe", 0)
    monkeypatch.setitem(gate._read_bounded_regular.__kwdefaults__, "_max_bytes", 1)
    monkeypatch.setitem(gate._canonical_sha256.__kwdefaults__, "_json_dumps", lambda *_a, **_k: "{}")
    monkeypatch.setitem(gate._exact_utc.__kwdefaults__, "_strptime", lambda *_: None)
    assert verify(paths) == expected


def test_public_function_defaults_cannot_turn_noncash_hold_into_go(monkeypatch, tmp_path):
    req = request(unit_type="NONCASH")
    receipt = pwev.compile_paid_work_effort_value_gate(req)
    paths = retain(tmp_path, req, receipt)
    assert receipt["decision"] == "HOLD_VALUE_UNKNOWN"
    monkeypatch.setitem(gate._strict_json.__kwdefaults__, "_json_loads", lambda *_a, **_k: {})
    monkeypatch.setitem(gate._read_bounded_regular.__kwdefaults__, "_max_bytes", 1)
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        verify(paths)
    assert caught.value.code == "ECONOMICS_HOLD_VALUE_UNKNOWN"


def copied_generation(tmp_path):
    for filename in (*SOURCE_FILES, "_claim_economic_generation.py"):
        shutil.copyfile(ROOT / "concierge" / filename, tmp_path / filename)
    return runpy.run_path(str(tmp_path / "_claim_economic_generation.py"))["load_claim_generation"]


def test_off_posix_ctime_precision_does_not_reject_same_source_generation(monkeypatch, tmp_path):
    import os as real_os

    fake_os = types.ModuleType("os")
    fake_os.__dict__.update(vars(real_os))
    fake_os.name = "nt"
    real_lstat = real_os.lstat

    def skewed_lstat(path):
        st = real_lstat(path)
        return types.SimpleNamespace(
            st_dev=st.st_dev,
            st_ino=st.st_ino,
            st_mode=st.st_mode,
            st_size=st.st_size,
            st_mtime_ns=st.st_mtime_ns,
            st_ctime_ns=st.st_ctime_ns + 7_000_000,
        )

    fake_os.lstat = skewed_lstat
    with monkeypatch.context() as patched:
        patched.setitem(sys.modules, "os", fake_os)
        load = copied_generation(tmp_path)

    verifier = load(gate.ClaimEconomicAdmissionError)
    assert verifier.__defaults__ is None
    assert verifier.__kwdefaults__ is None


def test_cold_generation_preserves_public_module_identity(tmp_path):
    modules = {name: sys.modules[name] for name in (
        "concierge.paid_work_effort_value_gate", "concierge.fleet_economic_admission",
        "concierge._fleet_economic_admission_v1", "concierge.claim_economic_admission",
    )}
    load = copied_generation(tmp_path)
    verifier = load(gate.ClaimEconomicAdmissionError)
    assert verifier.__defaults__ is None
    assert verifier.__kwdefaults__ is None
    req = request()
    paths = retain(tmp_path, req, pwev.compile_paid_work_effort_value_gate(req))
    assert verifier("acme/widget", 42, payoff(), *paths, decision_as_of=AS_OF) == verify(paths)
    assert all(sys.modules[name] is module for name, module in modules.items())


@pytest.mark.parametrize("filename", SOURCE_FILES)
def test_changed_source_pin_fails_without_public_fallback(tmp_path, filename):
    load = copied_generation(tmp_path)
    with (tmp_path / filename).open("ab") as output:
        output.write(b"\n# changed generation\n")
    with pytest.raises(ImportError, match="source-pinned claim economics generation unavailable"):
        load(gate.ClaimEconomicAdmissionError)


@pytest.mark.parametrize("kind", ["missing", "empty", "directory", "symlink"])
def test_bad_source_custody_fails_closed(tmp_path, kind):
    load = copied_generation(tmp_path)
    source = tmp_path / "paid_work_effort_value_gate.py"
    payload = source.read_bytes()
    source.unlink()
    if kind == "empty":
        source.write_bytes(b"")
    elif kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        target = tmp_path / "elsewhere.py"
        target.write_bytes(payload)
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("host cannot create a symlink")
    with pytest.raises(ImportError, match="source-pinned claim economics generation unavailable"):
        load(gate.ClaimEconomicAdmissionError)


def test_entrypoint_rejects_forged_rate_receipt_before_provider_reads(monkeypatch, tmp_path):
    from concierge import entrypoint as e

    req = request(amount="110", hours="1.01")
    assert pwev.compile_paid_work_effort_value_gate(req)["decision"] == "SKIP_ECONOMICS"
    monkeypatch.setattr(pwev, "_currency_floor", lambda *_: (Decimal(0), Decimal(0)))
    forged = pwev.compile_paid_work_effort_value_gate(req)
    assert forged["decision"] == "GO"
    paths = retain(tmp_path, req, forged)
    seen = []
    monkeypatch.setattr(e, "verify_claim_payoff_bundle", lambda *_: payoff())
    monkeypatch.setattr(e, "preflight_bounty", lambda *_: seen.append("provider_preflight"))
    monkeypatch.setattr(e, "inspect_bounty_availability", lambda *_: seen.append("availability"))
    now = datetime(2026, 9, 17, 20, 30, tzinfo=timezone.utc)
    preflight = e._build_preflight_claim(
        gate.verify_claim_economic_receipt, now=lambda _tz: now, utc=timezone.utc,
    )
    argv = ["claim", "--repo", "acme/widget", "--issue", "42", "--wallet", "alice",
            "--payoff-bundle", str(tmp_path / "unused-payoff"),
            "--economic-request", str(paths[0]), "--economic-receipt", str(paths[1])]
    with pytest.raises(gate.ClaimEconomicAdmissionError) as caught:
        preflight(argv)
    assert caught.value.code == "ECONOMIC_RECEIPT_REPLAY_MISMATCH"
    assert seen == []
