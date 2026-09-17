from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from concierge.work_reward_ledger import (
    EVENTS_SCHEMA_VERSION,
    WorkRewardLedgerConflict,
    WorkRewardLedgerError,
    compile_ledger,
    merge_event_streams,
    render_markdown,
    verify_ledger,
)


def evidence(n: int, when: str, *, synthetic: bool = True):
    return {
        "source_uri": f"https://example.invalid/evidence/{n}",
        "source_kind": "synthetic_fixture" if synthetic else "provider_receipt",
        "observed_at": when,
        "sha256": f"{n:064x}",
    }


def amount(value: str, unit: str):
    return {"value": value, "unit": unit}


def event(
    sequence: int,
    state: str,
    when: str,
    *,
    work_id: str = "frantic:example/repo#384",
    provider: str = "Frantic",
    program: str = "bug-bounty",
    advertised=None,
    award=None,
    reference=None,
    rail=None,
    settlement=None,
):
    return {
        "work_id": work_id,
        "sequence": sequence,
        "state": state,
        "provider": provider,
        "program": program,
        "synthetic": True,
        "evidence": evidence(sequence, when),
        "advertised_amount": advertised,
        "award_amount": award,
        "provider_reference": reference,
        "payment_rail": rail,
        "settlement": settlement,
    }


def receipt(receipt_id: str, value: str, unit: str, when: str, n: int = 900):
    return {
        "receipt_id": receipt_id,
        "amount": amount(value, unit),
        "source_uri": f"https://example.invalid/settlement/{receipt_id}",
        "observed_at": when,
        "sha256": f"{n:064x}",
    }


def fiat_payload():
    states = [
        event(1, "advertised", "2026-09-01T00:00:00Z", advertised=amount("90", "USD")),
        event(2, "claimed", "2026-09-01T01:00:00Z", reference={"kind": "claim", "value": "claim-1"}),
        event(3, "submitted", "2026-09-02T00:00:00Z", reference={"kind": "submission", "value": "pr-384"}),
        event(4, "accepted", "2026-09-03T00:00:00Z"),
        event(5, "awarded", "2026-09-04T00:00:00Z", award=amount("90", "USD"), reference={"kind": "award", "value": "award-77"}),
        event(6, "payment_pending", "2026-09-05T00:00:00Z", rail="stripe", reference={"kind": "ticket", "value": "ticket-9"}),
        event(7, "paid", "2026-09-06T00:00:00Z", rail="stripe", settlement=receipt("stripe-pi-1", "90", "USD", "2026-09-06T00:00:01Z")),
    ]
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": states}


def rtc_payload():
    work = "rustchain:rustchain-bounties#315"
    return {
        "schema_version": EVENTS_SCHEMA_VERSION,
        "events": [
            event(1, "advertised", "2026-09-01T00:00:00Z", work_id=work, provider="RustChain", program="native-bounty", advertised=amount("3000", "RTC")),
            event(2, "awarded", "2026-09-07T00:00:00Z", work_id=work, provider="RustChain", program="native-bounty", award=amount("3000", "RTC"), reference={"kind": "award", "value": "award-315"}),
            event(3, "paid", "2026-09-08T00:00:00Z", work_id=work, provider="RustChain", program="native-bounty", settlement=receipt("rtc-tx-1", "3000", "RTC", "2026-09-08T00:00:01Z", 901)),
        ],
    }


def test_cross_program_paid_totals_remain_partitioned_by_denomination():
    ledger = merge_event_streams([fiat_payload(), rtc_payload()])
    assert ledger["recognized_paid_totals"] == [
        {"unit": "RTC", "amount": "3000"},
        {"unit": "USD", "amount": "90"},
    ]
    assert ledger["work"][0]["recognized_paid_amount"] == amount("90", "USD")
    assert ledger["work"][1]["recognized_paid_amount"] == amount("3000", "RTC")
    assert verify_ledger(ledger) == ledger
    text = render_markdown(ledger)
    assert "90 USD" in text
    assert "3000 RTC" in text
    assert "never converted" in text


def test_acceptance_is_not_payment_and_rail_is_not_transfer():
    payload = fiat_payload()
    payload["events"] = payload["events"][:6]
    ledger = compile_ledger(payload)
    row = ledger["work"][0]
    assert row["state"] == "payment_pending"
    assert row["payment_rails"] == ["stripe"]
    assert row["settlements"] == []
    assert row["recognized_paid_amount"] == amount("0", "USD")
    assert ledger["recognized_paid_totals"] == [{"unit": "USD", "amount": "0"}]


def test_paid_requires_explicit_settlement_receipt():
    payload = fiat_payload()
    payload["events"][-1]["settlement"] = None
    with pytest.raises(WorkRewardLedgerError, match="requires a settlement receipt"):
        compile_ledger(payload)


def test_state_regression_is_rejected():
    payload = fiat_payload()
    payload["events"][4]["state"] = "claimed"
    payload["events"][4]["award_amount"] = None
    with pytest.raises(WorkRewardLedgerConflict, match="state regression"):
        compile_ledger(payload)


def test_reward_denomination_mutation_is_rejected():
    payload = fiat_payload()
    payload["events"][4]["award_amount"] = amount("90", "EUR")
    with pytest.raises(WorkRewardLedgerConflict, match="award denomination"):
        compile_ledger(payload)


def test_token_to_usd_invention_is_rejected():
    payload = rtc_payload()
    payload["events"][-1]["settlement"] = receipt("rtc-tx-1", "3000", "USD", "2026-09-08T00:00:01Z")
    with pytest.raises(WorkRewardLedgerConflict, match="settlement denomination"):
        compile_ledger(payload)


def test_duplicate_receipt_identity_cannot_pay_two_work_items():
    left = fiat_payload()
    right = copy.deepcopy(fiat_payload())
    for item in right["events"]:
        item["work_id"] = "frantic:example/repo#385"
        item["evidence"]["sha256"] = "b" * 64
    with pytest.raises(WorkRewardLedgerConflict, match="receipt_id is already bound"):
        merge_event_streams([left, right])


def test_stale_later_evidence_is_rejected():
    payload = fiat_payload()
    payload["events"][3]["evidence"]["observed_at"] = "2026-09-01T12:00:00Z"
    with pytest.raises(WorkRewardLedgerConflict, match="older than prior evidence"):
        compile_ledger(payload)


def test_settlement_receipt_cannot_predate_paid_event_evidence():
    payload = fiat_payload()
    payload["events"][-1]["settlement"]["observed_at"] = "2026-09-05T00:00:00Z"
    with pytest.raises(WorkRewardLedgerError, match="predates its paid event"):
        compile_ledger(payload)


def test_merge_coalesces_exact_duplicate_events_but_rejects_conflicts():
    left = fiat_payload()
    right = {"schema_version": EVENTS_SCHEMA_VERSION, "events": [copy.deepcopy(left["events"][0])]}
    # A partial stream alone is fine to normalize; merge with complete stream coalesces the duplicate.
    assert merge_event_streams([left, right]) == compile_ledger(left)
    conflict = copy.deepcopy(right)
    conflict["events"][0]["advertised_amount"] = amount("91", "USD")
    with pytest.raises(WorkRewardLedgerConflict, match="conflict at one work sequence"):
        merge_event_streams([left, conflict])


def test_tampered_sealed_summary_fails_verification():
    ledger = compile_ledger(fiat_payload())
    tampered = copy.deepcopy(ledger)
    tampered["work"][0]["state"] = "accepted"
    with pytest.raises(WorkRewardLedgerError, match="digest mismatch"):
        verify_ledger(tampered)


def test_work_identity_metadata_cannot_mutate():
    payload = fiat_payload()
    payload["events"][2]["provider"] = "OtherProvider"
    with pytest.raises(WorkRewardLedgerConflict, match="identity metadata changed"):
        compile_ledger(payload)


def test_non_contiguous_event_history_fails_closed():
    payload = fiat_payload()
    payload["events"].pop(2)
    for item in payload["events"][2:]:
        # preserve original sequence numbers to expose the history gap
        pass
    with pytest.raises(WorkRewardLedgerConflict, match="contiguous"):
        compile_ledger(payload)


def test_optimized_python_preserves_safety_checks(tmp_path: Path):
    payload = fiat_payload()
    payload["events"][-1]["settlement"] = None
    source = tmp_path / "events.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    code = (
        "from concierge.work_reward_ledger import load_json, compile_ledger; "
        f"compile_ledger(load_json({str(source)!r}))"
    )
    completed = subprocess.run(
        [sys.executable, "-O", "-c", code],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "requires a settlement receipt" in completed.stderr
