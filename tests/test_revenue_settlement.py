# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import json

import pytest

from concierge import revenue_settlement as rs
from concierge.revenue_settlement import (
    RevenueSettlementEvidenceError,
    RevenueSettlementInputError,
    history_envelope_sha256,
    history_row_sha256,
    inventory_history,
    reconcile_cash,
    summarize_cash,
)


def closeout(*, pr=7, amount="10", state="MERGED", currency="RTC"):
    return {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": state,
        "currency": currency,
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }


def incoming(*, amount=10, sender="sponsor", tag=1):
    return {
        "type": "transfer_in",
        "amount": amount,
        "epoch": 200 + tag,
        "timestamp": 1772848800 + tag,
        "tx_hash": f"tx-{tag}",
        "from": sender,
    }


def outgoing(*, amount=10, recipient="bob", tag=1, status=None):
    row = {
        "type": "transfer_out",
        "amount": amount,
        "epoch": 200 + tag,
        "timestamp": 1772848800 + tag,
        "tx_hash": f"out-{tag}",
        "to": recipient,
    }
    if status is not None:
        row["status"] = status
    return row


def reward(*, amount=10, tag=1):
    return {
        "type": "reward",
        "amount": amount,
        "epoch": 200 + tag,
        "timestamp": 1772848800 + tag,
        "tx_hash": None,
    }


def ledger(*, amount=10, tag=1):
    return {
        "type": "ledger",
        "amount": amount,
        "epoch": 200 + tag,
        "timestamp": 1772848800 + tag,
        "tx_hash": None,
        "reason": "manual_adjustment",
    }


def history(*rows, wallet="alice", total=None):
    return {
        "ok": True,
        "miner_id": wallet,
        "transactions": list(rows),
        "total": len(rows) if total is None else total,
    }


def bind(item, *rows, wallet="alice"):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [history_row_sha256(row, wallet) for row in rows],
    }


def test_unbound_merge_does_not_become_cash():
    item = closeout()
    row = incoming()
    result = reconcile_cash([item], history(row), [], wallet="alice")
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_exact_current_transfer_in_shape_is_verified():
    item = closeout()
    row = incoming()
    snapshot = history(row)
    result = reconcile_cash(
        [item], snapshot, [bind(item, row)], wallet="alice"
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"
    evidence = result[0]["payment_evidence"][0]
    assert evidence["history_sha256"] == history_row_sha256(row, "alice")
    assert evidence["history_envelope_sha256"] == history_envelope_sha256(snapshot)
    assert evidence["type"] == "transfer_in"
    assert evidence["tx_hash"] == "tx-1"
    assert evidence["status"] == "confirmed"


def test_multiple_explicit_rows_can_sum_to_exact_award():
    item = closeout(amount="10")
    first = incoming(amount="4.25", tag=1)
    second = incoming(amount="5.75", tag=2)
    result = reconcile_cash(
        [item],
        history(first, second),
        [bind(item, first, second)],
        wallet="alice",
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_partial_bound_payment_is_not_upgraded_to_paid():
    item = closeout(amount="10")
    row = incoming(amount="4")
    result = reconcile_cash(
        [item], history(row), [bind(item, row)], wallet="alice"
    )
    assert result[0]["cash_status"] == "partially_verified"
    assert result[0]["verified_amount"] == "4"


@pytest.mark.parametrize("factory", [outgoing, reward, ledger])
def test_non_incoming_canonical_rows_cannot_settle_bounty(factory):
    item = closeout()
    row = factory()
    with pytest.raises(RevenueSettlementEvidenceError, match="not canonical incoming"):
        reconcile_cash(
            [item], history(row), [bind(item, row)], wallet="alice"
        )


def test_arbitrary_note_row_cannot_manufacture_verified_paid():
    item = closeout()
    row = {
        "type": "note",
        "amount": 10,
        "to": "alice",
        "timestamp": 1772848800,
    }
    binding = {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [history_row_sha256(row, "alice")],
    }
    with pytest.raises(RevenueSettlementEvidenceError, match="unsupported transaction type"):
        reconcile_cash([item], history(row), [binding], wallet="alice")


def test_bare_history_list_is_not_cash_authority():
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="canonical RustChain history envelope"):
        inventory_history([row], "alice")


def test_offline_envelope_must_match_expected_wallet():
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="another wallet"):
        inventory_history(history(row, wallet="mallory"), "alice")


def test_row_fingerprint_is_bound_to_recipient_wallet_provenance():
    row = incoming()
    assert history_row_sha256(row, "alice") != history_row_sha256(row, "mallory")
    with pytest.raises(RevenueSettlementInputError, match="wallet provenance"):
        history_row_sha256(row)


def test_binding_for_other_wallet_row_is_absent_even_if_transaction_same():
    item = closeout()
    row = incoming()
    mallory_binding = bind(item, row, wallet="mallory")
    with pytest.raises(RevenueSettlementEvidenceError, match="is absent"):
        reconcile_cash([item], history(row, wallet="alice"), [mallory_binding], wallet="alice")


def test_envelope_requires_exact_current_fields():
    row = incoming()
    snapshot = history(row)
    snapshot["schema_version"] = 1
    with pytest.raises(RevenueSettlementEvidenceError, match="fields are not canonical"):
        inventory_history(snapshot, "alice")


@pytest.mark.parametrize(
    "total",
    [-1, True, "1", 0],
)
def test_envelope_total_must_cover_returned_transactions(total):
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="pagination is malformed"):
        inventory_history(history(row, total=total), "alice")


def test_incomplete_history_page_cannot_be_cash_authority():
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="complete snapshot"):
        inventory_history(history(row, total=2), "alice")


def test_incoming_transfer_rejects_invented_recipient_alias():
    row = incoming()
    row["to"] = "alice"
    with pytest.raises(RevenueSettlementEvidenceError, match="incoming transfer row fields"):
        inventory_history(history(row), "alice")


def test_incoming_transfer_rejects_status_field():
    row = incoming()
    row["status"] = "confirmed"
    with pytest.raises(RevenueSettlementEvidenceError, match="incoming transfer row fields"):
        inventory_history(history(row), "alice")


def test_incoming_transfer_requires_sender_and_transaction_identity():
    row = incoming()
    row.pop("from")
    with pytest.raises(RevenueSettlementEvidenceError, match="incoming transfer row fields"):
        inventory_history(history(row), "alice")
    row = incoming()
    row["tx_hash"] = None
    with pytest.raises(RevenueSettlementEvidenceError, match="omitted transaction identity"):
        inventory_history(history(row), "alice")


def test_duplicate_indistinguishable_history_rows_are_rejected():
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="duplicate indistinguishable"):
        inventory_history(history(row, copy.deepcopy(row)), "alice")


def test_fingerprint_is_key_order_independent_and_mutation_sensitive():
    row = incoming()
    reordered = {key: row[key] for key in reversed(list(row))}
    assert history_row_sha256(row, "alice") == history_row_sha256(reordered, "alice")
    changed = dict(row)
    changed["amount"] = 9
    assert history_row_sha256(row, "alice") != history_row_sha256(changed, "alice")


def test_same_history_row_cannot_pay_two_awards():
    first = closeout(pr=7)
    second = closeout(pr=8)
    row = incoming()
    bindings = [bind(first, row), bind(second, row)]
    with pytest.raises(RevenueSettlementInputError, match="cannot settle multiple"):
        reconcile_cash([first, second], history(row), bindings, wallet="alice")


def test_missing_bound_row_fails_closed():
    item = closeout()
    selected = incoming(tag=1)
    actual = incoming(tag=2)
    with pytest.raises(RevenueSettlementEvidenceError, match="is absent"):
        reconcile_cash(
            [item], history(actual), [bind(item, selected)], wallet="alice"
        )


def test_overpayment_is_not_silently_counted():
    item = closeout(amount="10")
    row = incoming(amount=11)
    with pytest.raises(RevenueSettlementEvidenceError, match="exceed"):
        reconcile_cash([item], history(row), [bind(item, row)], wallet="alice")


def test_non_rtc_closeout_cannot_use_rustchain_history():
    item = closeout(currency="USD")
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="not denominated in RTC"):
        reconcile_cash([item], history(row), [bind(item, row)], wallet="alice")


def test_unmerged_item_cannot_have_payment_bound_as_cash():
    item = closeout(state="OPEN")
    row = incoming()
    with pytest.raises(RevenueSettlementEvidenceError, match="not merged"):
        reconcile_cash([item], history(row), [bind(item, row)], wallet="alice")


def test_binding_unknown_closeout_item_is_rejected():
    item = closeout()
    row = incoming()
    unknown = {
        "repo": "Sponsor/project",
        "pr": 99,
        "history_sha256s": [history_row_sha256(row, "alice")],
    }
    with pytest.raises(RevenueSettlementInputError, match="unknown closeout item"):
        reconcile_cash([item], history(row), [unknown], wallet="alice")


def test_cash_status_must_start_not_inferred():
    item = closeout()
    item["cash_status"] = "paid"
    with pytest.raises(RevenueSettlementInputError, match="must be not_inferred"):
        reconcile_cash([item], history(), [], wallet="alice")


@pytest.mark.parametrize("wallet", ["", " alice", "alice ", "alice bob", "alice\nbob"])
def test_wallet_identity_is_strict(wallet):
    with pytest.raises(RevenueSettlementInputError, match="wallet"):
        inventory_history(history(wallet="alice"), wallet)


def test_summary_counts_only_evidence_backed_amounts():
    first = closeout(pr=7, amount="10")
    second = closeout(pr=8, amount="8")
    third = closeout(pr=9, amount="3")
    paid = incoming(amount=10, tag=1)
    partial = incoming(amount=2.5, tag=2)
    results = reconcile_cash(
        [first, second, third],
        history(paid, partial),
        [bind(first, paid), bind(second, partial)],
        wallet="alice",
    )
    assert summarize_cash(results) == {
        "currency": "RTC",
        "verified_cash_total": "12.5",
        "partial_cash_total": "2.5",
        "fully_paid_items": 1,
        "partially_paid_items": 1,
        "unverified_items": 1,
        "cash_claim": "wallet_history_evidence_only",
    }


def test_boolean_and_nonfinite_closeout_amounts_are_rejected():
    with pytest.raises(RevenueSettlementInputError, match="decimal"):
        reconcile_cash([closeout(amount=True)], history(), [], wallet="alice")
    with pytest.raises(RevenueSettlementInputError, match="bounded positive"):
        reconcile_cash([closeout(amount="NaN")], history(), [], wallet="alice")


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text('{"schema_version":1,"schema_version":1,"items":[]}', encoding="utf-8")
    with pytest.raises(RevenueSettlementInputError, match="duplicate"):
        rs._load_json(str(path))


def test_lone_surrogate_cannot_escape_canonicalization():
    row = incoming()
    row["from"] = "\ud800"
    with pytest.raises(RevenueSettlementInputError, match="canonical JSON"):
        history_row_sha256(row, "alice")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


def test_live_fetch_preserves_and_validates_canonical_envelope(monkeypatch):
    row = incoming()
    snapshot = history(row)
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(snapshot)
    monkeypatch.setattr(rs._payout_tracker.requests, "get", fake_get)
    result = rs._fetch_canonical_history("alice", node_url="https://node.example")
    assert result is snapshot
    assert calls[0][1]["params"] == {"miner_id": "alice", "limit": 200}
    assert calls[0][1]["verify"] is True


def test_live_fetch_rejects_legacy_bare_array(monkeypatch):
    row = incoming()
    monkeypatch.setattr(
        rs._payout_tracker.requests,
        "get",
        lambda *args, **kwargs: FakeResponse([row]),
    )
    with pytest.raises(rs.PayoutLookupError, match="canonical history"):
        rs._fetch_canonical_history("alice", node_url="https://node.example")
