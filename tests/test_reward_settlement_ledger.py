from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


def check(condition, message=None):
    if not condition:
        raise AssertionError(message or "check failed")


from concierge.reward_settlement_ledger import (
    RewardSettlementEvidenceError,
    RewardSettlementInputError,
    compile_document,
    paid_event_from_reconciliation,
    verify_document,
)


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def event(kind: str, source_type: str, minute: int, **extra):
    row = {
        "kind": kind,
        "source_type": source_type,
        "source_ref": f"provider:{kind.lower()}:{minute}",
        "source_sha256": sha(f"{kind}:{minute}"),
        "observed_at": f"2026-09-16T20:{minute:02d}:00Z",
    }
    row.update(extra)
    return row


def money(amount: str, code: str = "USD", kind: str = "currency"):
    return {"kind": kind, "code": code, "amount": amount}


def source(*events):
    return {"schema_version": 1, "items": [{"work_id": "example#42", "repo": "acme/project", "pr": 42, "events": list(events)}]}


def test_merge_is_not_an_event_or_payment():
    compiled = compile_document({"schema_version": 1, "items": [{"work_id": "merged-pr", "repo": "acme/project", "pr": 7, "events": []}]})
    row = compiled["records"][0]
    check(row["state"] == "ELIGIBILITY_UNKNOWN")
    check(row["claims"]["awarded"] is None)
    check(row["claims"]["paid"] is None)
    check(row["claims"]["revenue_recognition"] == "not_computed")


def test_full_explicit_chain_is_paid():
    compiled = compile_document(source(
        event("ADVERTISED", "sponsor_publication", 1, denomination=money("90")),
        event("AWARDED", "sponsor_decision", 2, denomination=money("90")),
        event("TICKET_OPENED", "payout_ticket", 3),
        event("RAIL_SUPPLIED", "payment_rail", 4),
        event("TRANSFER_EVIDENCED", "transfer_evidence", 5, denomination=money("90"), transfer_status="confirmed"),
        event("PAID", "verified_cash", 6, denomination=money("90"), cash_status="verified_paid"),
    ))
    row = compiled["records"][0]
    check(row["state"] == "PAID")
    check(row["evidence_gaps"] == [])
    check(row["claims"]["paid"] == money("90"))
    check(verify_document(compiled)["verified"] is True)


def test_paid_without_prior_receipts_keeps_evidence_gaps_not_fictions():
    compiled = compile_document(source(event("PAID", "verified_cash", 6, denomination=money("10"), cash_status="verified_paid")))
    row = compiled["records"][0]
    check(row["state"] == "PAID")
    check(row["evidence_gaps"] == ["ADVERTISED", "AWARDED", "TICKET_OPENED", "RAIL_SUPPLIED", "TRANSFER_EVIDENCED"])


def test_advertised_is_not_awarded():
    row = compile_document(source(event("ADVERTISED", "sponsor_publication", 1, denomination=money("500"))))["records"][0]
    check(row["state"] == "ADVERTISED")
    check(row["claims"]["awarded"] is None)
    check(row["claims"]["paid"] is None)


def test_transfer_evidence_is_not_paid():
    row = compile_document(source(event("TRANSFER_EVIDENCED", "transfer_evidence", 5, denomination=money("90"), transfer_status="confirmed")))["records"][0]
    check(row["state"] == "TRANSFER_EVIDENCED")
    check(row["claims"]["paid"] is None)


def test_paid_requires_verified_cash_authority():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(event("PAID", "transfer_evidence", 5, denomination=money("90"), cash_status="verified_paid")))
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(event("PAID", "verified_cash", 5, denomination=money("90"), cash_status="partially_verified")))


def test_transfer_requires_confirmed_status():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(event("TRANSFER_EVIDENCED", "transfer_evidence", 5, denomination=money("90"), transfer_status="pending")))


def test_closed_without_reward_is_terminal_and_conflicts_with_paid():
    row = compile_document(source(event("CLOSED_WITHOUT_REWARD", "sponsor_closeout", 7)))["records"][0]
    check(row["state"] == "CLOSED_WITHOUT_REWARD")
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("CLOSED_WITHOUT_REWARD", "sponsor_closeout", 7),
            event("PAID", "verified_cash", 8, denomination=money("10"), cash_status="verified_paid"),
        ))


def test_award_cannot_exceed_advertised_when_same_denomination():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("ADVERTISED", "sponsor_publication", 1, denomination=money("50")),
            event("AWARDED", "sponsor_decision", 2, denomination=money("60")),
        ))


def test_payment_cannot_exceed_award_when_same_denomination():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("AWARDED", "sponsor_decision", 2, denomination=money("50")),
            event("PAID", "verified_cash", 3, denomination=money("60"), cash_status="verified_paid"),
        ))


def test_non_cash_units_remain_units_and_are_never_converted():
    row = compile_document(source(event("ADVERTISED", "sponsor_publication", 1, denomination=money("1000", "POINTS", "unit"))))["records"][0]
    check(row["claims"]["advertised"] == {"kind": "unit", "code": "POINTS", "amount": "1000"})
    check(row["claims"]["paid"] is None)


def test_denomination_change_requires_separate_conversion_evidence():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("ADVERTISED", "sponsor_publication", 1, denomination=money("1000", "POINTS", "unit")),
            event("AWARDED", "sponsor_decision", 2, denomination=money("100", "USD", "currency")),
        ))
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("AWARDED", "sponsor_decision", 2, denomination=money("90", "USD")),
            event("PAID", "verified_cash", 3, denomination=money("90", "RTC"), cash_status="verified_paid"),
        ))


def test_conflicting_same_kind_amounts_fail_closed():
    with pytest.raises(RewardSettlementEvidenceError):
        compile_document(source(
            event("AWARDED", "sponsor_decision", 2, denomination=money("50")),
            event("AWARDED", "sponsor_decision", 3, denomination=money("45")),
        ))


def test_duplicate_evidence_event_is_rejected():
    e = event("ADVERTISED", "sponsor_publication", 1, denomination=money("50"))
    with pytest.raises(RewardSettlementInputError):
        compile_document(source(e, copy.deepcopy(e)))


def test_document_order_is_deterministic():
    first = {"work_id": "z", "events": [event("ADVERTISED", "sponsor_publication", 1, denomination=money("1"))]}
    second = {"work_id": "A", "events": []}
    a = compile_document({"schema_version": 1, "items": [first, second]})
    b = compile_document({"schema_version": 1, "items": [second, first]})
    check(a == b)
    check([r["work_id"] for r in a["records"]] == ["A", "z"])


def test_verify_rejects_semantic_tamper_even_if_old_receipt_retained():
    compiled = compile_document(source(event("ADVERTISED", "sponsor_publication", 1, denomination=money("50"))))
    tampered = copy.deepcopy(compiled)
    tampered["records"][0]["events"][0]["denomination"]["amount"] = "5000"
    with pytest.raises(RewardSettlementEvidenceError):
        verify_document(tampered)


def test_verify_rejects_derived_state_tamper():
    compiled = compile_document(source(event("ADVERTISED", "sponsor_publication", 1, denomination=money("50"))))
    tampered = copy.deepcopy(compiled)
    tampered["records"][0]["state"] = "PAID"
    with pytest.raises(RewardSettlementEvidenceError):
        verify_document(tampered)


def test_lift_existing_verified_cash_reconciliation_only():
    lifted = paid_event_from_reconciliation(
        {"cash_status": "verified_paid", "currency": "RTC", "verified_amount": "90.000"},
        source_ref="wallet-history:receipt-1",
        source_sha256=sha("cash-reconciliation"),
        observed_at="2026-09-16T20:06:00Z",
    )
    check(lifted["kind"] == "PAID")
    check(lifted["denomination"] == money("90", "RTC"))
    compile_document(source(lifted))
    for state in ("partially_verified", "not_inferred"):
        with pytest.raises(RewardSettlementEvidenceError):
            paid_event_from_reconciliation(
                {"cash_status": state, "currency": "RTC", "verified_amount": "45"},
                source_ref="wallet-history:x",
                source_sha256=sha(state),
                observed_at="2026-09-16T20:06:00Z",
            )


def test_repo_pr_and_work_identity_are_strict():
    with pytest.raises(RewardSettlementInputError):
        compile_document({"schema_version": 1, "items": [{"work_id": "x", "repo": "../oops", "pr": 1, "events": []}]})
    with pytest.raises(RewardSettlementInputError):
        compile_document({"schema_version": 1, "items": [{"work_id": "x", "repo": "a/b", "pr": True, "events": []}]})


def test_observed_at_requires_utc_z():
    bad = event("ADVERTISED", "sponsor_publication", 1, denomination=money("1"))
    bad["observed_at"] = "2026-09-16T20:01:00-04:00"
    with pytest.raises(RewardSettlementInputError):
        compile_document(source(bad))


def test_source_digest_is_lowercase_sha256():
    bad = event("ADVERTISED", "sponsor_publication", 1, denomination=money("1"))
    bad["source_sha256"] = "A" * 64
    with pytest.raises(RewardSettlementInputError):
        compile_document(source(bad))


def test_cli_compile_verify_and_strict_duplicate_json(tmp_path: Path):
    src = tmp_path / "source.json"
    out = tmp_path / "compiled.json"
    src.write_text(json.dumps(source(event("ADVERTISED", "sponsor_publication", 1, denomination=money("25")))), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "concierge.reward_settlement_ledger", "compile", str(src), "-o", str(out)], text=True, capture_output=True)
    check(proc.returncode == 0, proc.stderr)
    proc = subprocess.run([sys.executable, "-m", "concierge.reward_settlement_ledger", "verify", str(out)], text=True, capture_output=True)
    check(proc.returncode == 0, proc.stderr)
    check(json.loads(proc.stdout)["verified"] is True)

    duplicate = tmp_path / "dup.json"
    duplicate.write_text('{"schema_version":1,"items":[],"items":[]}', encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "concierge.reward_settlement_ledger", "compile", str(duplicate)], text=True, capture_output=True)
    check(proc.returncode == 2)
    check("duplicate JSON key" in proc.stderr)


def test_cli_rejects_nan(tmp_path: Path):
    bad = tmp_path / "nan.json"
    bad.write_text('{"schema_version":1,"items":NaN}', encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "concierge.reward_settlement_ledger", "compile", str(bad)], text=True, capture_output=True)
    check(proc.returncode == 2)
    check("non-finite JSON constant" in proc.stderr)
