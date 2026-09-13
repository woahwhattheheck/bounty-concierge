# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import revenue_settlement as rs
from concierge.revenue_settlement import (
    RevenueSettlementEvidenceError,
    RevenueSettlementInputError,
    history_row_sha256,
    inventory_history,
    reconcile_cash,
    summarize_cash,
)


def closeout(*, pr=7, amount="10", state="MERGED", currency="RTC"):
    row = {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": state,
        "currency": currency,
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }
    if state == "MERGED":
        row["merged_at"] = "1970-01-01T00:00:00Z"
    return row


def payment(*, amount=10, sender="treasury", status=None, tag="a"):
    timestamp = (
        "2026-09-13T00:00:01Z"
        if tag == "a"
        else f"2026-09-13T00:00:0{tag}Z"
    )
    row = {
        "type": "transfer_in",
        "amount": amount,
        "from": sender,
        "timestamp": timestamp,
        "tx_hash": f"tx-{tag}",
    }
    if status is not None:
        row["status"] = status
    return row


def legacy_payment(*, amount=10, sender="treasury", to="alice", tag="a"):
    created_at = (
        "2026-09-13T00:00:01Z"
        if tag == "a"
        else f"2026-09-13T00:00:0{tag}Z"
    )
    return {
        "amount_rtc": amount,
        "from": sender,
        "to": to,
        "created_at": created_at,
        "tx_hash": f"legacy-{tag}",
    }


def bind(item, *rows, wallet="alice"):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [history_row_sha256(row, wallet=wallet) for row in rows],
    }


def reconcile(items, history, bindings, *, wallet="alice", history_wallet=None, source="captured_wallet"):
    return reconcile_cash(
        items,
        history,
        bindings,
        wallet=wallet,
        history_wallet=wallet if history_wallet is None else history_wallet,
        history_source=source,
    )


def test_default_helpers_remain_valid_under_temporal_authority():
    canonical_item = closeout(pr=70)
    canonical = payment()
    canonical_result = reconcile(
        [canonical_item],
        [canonical],
        [bind(canonical_item, canonical)],
    )
    assert canonical_result[0]["cash_status"] == "verified_paid"

    legacy_item = closeout(pr=71)
    legacy = legacy_payment()
    legacy_result = reconcile(
        [legacy_item],
        [legacy],
        [bind(legacy_item, legacy)],
    )
    assert legacy_result[0]["cash_status"] == "verified_paid"


def test_unbound_merge_does_not_become_cash():
    item = closeout()
    row = payment()
    result = reconcile([item], [row], [])
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_canonical_transfer_in_uses_history_wallet_as_recipient_authority():
    item = closeout()
    row = payment()
    assert "to" not in row
    result = reconcile([item], [row], [bind(item, row)])
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"
    assert result[0]["history_wallet"] == "alice"
    assert result[0]["payment_evidence"] == [
        {
            "history_sha256": history_row_sha256(row, wallet="alice"),
            "history_wallet": "alice",
            "amount_rtc": "10",
            "status": "confirmed",
            "evidence_kind": "canonical_transfer_in",
        }
    ]


def test_multiple_explicit_rows_can_sum_to_exact_award():
    item = closeout(amount="10")
    first = payment(amount="4.25", tag="1")
    second = payment(amount="5.75", tag="2")
    result = reconcile([item], [first, second], [bind(item, first, second)])
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_partial_bound_payment_is_not_upgraded_to_paid():
    item = closeout(amount="10")
    row = payment(amount="4")
    result = reconcile([item], [row], [bind(item, row)])
    assert result[0]["cash_status"] == "partially_verified"
    assert result[0]["verified_amount"] == "4"


@pytest.mark.parametrize("status", ["pending", "confirming", "failed"])
def test_nonterminal_bound_payment_fails_closed(status):
    item = closeout()
    row = payment(status=status)
    with pytest.raises(RevenueSettlementEvidenceError, match="not confirmed"):
        reconcile([item], [row], [bind(item, row)])


def test_unknown_provider_status_fails_before_matching():
    item = closeout()
    row = payment(status="mystery")
    with pytest.raises(RevenueSettlementEvidenceError, match="unknown settlement status"):
        reconcile([item], [row], [bind(item, row)])


def test_explicit_conflicting_recipient_on_canonical_incoming_fails_closed():
    item = closeout()
    row = payment()
    row["to"] = "mallory"
    with pytest.raises(RevenueSettlementEvidenceError, match="conflicts with wallet provenance"):
        reconcile([item], [row], [bind(item, row)])


def test_explicit_matching_recipient_on_canonical_incoming_is_tolerated():
    item = closeout()
    row = payment()
    row["to"] = "alice"
    result = reconcile([item], [row], [bind(item, row)])
    assert result[0]["cash_status"] == "verified_paid"


def test_canonical_incoming_requires_sender_identity():
    item = closeout()
    row = payment()
    del row["from"]
    with pytest.raises(RevenueSettlementEvidenceError, match="omitted sender identity"):
        reconcile([item], [row], [bind(item, row)])


def test_overpayment_is_not_silently_counted():
    item = closeout(amount="10")
    row = payment(amount="11")
    with pytest.raises(RevenueSettlementEvidenceError, match="exceed"):
        reconcile([item], [row], [bind(item, row)])


def test_non_rtc_closeout_cannot_use_rustchain_history():
    item = closeout(currency="USD")
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="not denominated in RTC"):
        reconcile([item], [row], [bind(item, row)])


def test_unmerged_item_cannot_have_payment_bound_as_closeout_cash():
    item = closeout(state="OPEN")
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="not merged"):
        reconcile([item], [row], [bind(item, row)])


def test_missing_bound_row_fails_closed():
    item = closeout()
    selected = payment(tag="1")
    actual = payment(tag="2")
    with pytest.raises(RevenueSettlementEvidenceError, match="is absent"):
        reconcile([item], [actual], [bind(item, selected)])


def test_same_history_row_cannot_pay_two_awards():
    first = closeout(pr=7)
    second = closeout(pr=8)
    row = payment()
    bindings = [bind(first, row), bind(second, row)]
    with pytest.raises(RevenueSettlementInputError, match="cannot settle multiple"):
        reconcile([first, second], [row], bindings)


def test_duplicate_indistinguishable_history_rows_are_rejected():
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="duplicate indistinguishable"):
        inventory_history([row, copy.deepcopy(row)], "alice", history_source="captured_wallet")


def test_conflicting_amount_fields_are_rejected_when_bound():
    item = closeout()
    row = payment()
    row["amount_rtc"] = 9
    with pytest.raises(RevenueSettlementEvidenceError, match="conflicting amount"):
        reconcile([item], [row], [bind(item, row)])


def test_unrelated_reward_and_ledger_rows_do_not_block_valid_payment():
    item = closeout()
    reward = {"type": "reward", "amount": 2, "timestamp": 1}
    ledger = {"type": "ledger", "amount": 3, "timestamp": 2}
    row = payment()
    result = reconcile([item], [reward, ledger, row], [bind(item, row)])
    assert result[0]["cash_status"] == "verified_paid"


@pytest.mark.parametrize(
    "row",
    [
        {"type": "reward", "amount": 2, "timestamp": 1},
        {"type": "ledger", "amount": 2, "timestamp": 1},
        {"type": "transfer_out", "amount": 2, "to": "bob", "timestamp": 1},
    ],
)
def test_non_incoming_rows_are_inventory_only_not_payment_evidence(row):
    inventory = inventory_history([row], "alice", history_source="captured_wallet")
    assert inventory[0]["bindable"] is False
    assert inventory[0]["history_sha256"] == history_row_sha256(row, wallet="alice")
    assert "incoming transfer" in inventory[0]["reason"]


def test_bound_outgoing_transfer_is_rejected():
    item = closeout()
    row = {"type": "transfer_out", "amount": 10, "to": "bob"}
    with pytest.raises(RevenueSettlementEvidenceError, match="not an incoming transfer"):
        reconcile([item], [row], [bind(item, row)])


def test_bound_self_funded_transfer_is_rejected():
    item = closeout()
    row = payment(sender="alice")
    with pytest.raises(RevenueSettlementEvidenceError, match="self-funded"):
        reconcile([item], [row], [bind(item, row)])


def test_unknown_typed_history_row_is_not_payment_evidence():
    item = closeout()
    row = {"type": "future_magic", "amount": 10, "from": "treasury"}
    with pytest.raises(RevenueSettlementEvidenceError, match="unknown transfer type"):
        reconcile([item], [row], [bind(item, row)])


def test_legacy_typeless_explicit_recipient_remains_supported():
    item = closeout()
    row = legacy_payment()
    result = reconcile([item], [row], [bind(item, row)])
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["payment_evidence"][0]["evidence_kind"] == "legacy_explicit_recipient"


def test_legacy_typeless_wrong_recipient_fails_closed():
    item = closeout()
    row = legacy_payment(to="mallory")
    with pytest.raises(RevenueSettlementEvidenceError, match="targets another wallet"):
        reconcile([item], [row], [bind(item, row)])


def test_legacy_typeless_row_without_recipient_is_not_bindable():
    item = closeout()
    row = {"amount_rtc": 10, "from": "treasury"}
    with pytest.raises(RevenueSettlementEvidenceError, match="omitted recipient identity"):
        reconcile([item], [row], [bind(item, row)])


@pytest.mark.parametrize("wallet", ["", " alice", "alice ", "alice bob", "alice\nbob"])
def test_wallet_identity_is_strict(wallet):
    with pytest.raises(RevenueSettlementInputError, match="wallet"):
        inventory_history([], wallet, history_source="captured_wallet")


def test_history_wallet_provenance_must_match_settlement_wallet():
    item = closeout()
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="provenance does not match"):
        reconcile([item], [row], [bind(item, row, wallet="alice")], wallet="alice", history_wallet="mallory")


def test_same_raw_row_has_different_evidence_identity_for_different_wallets():
    row = payment()
    assert history_row_sha256(row, wallet="alice") != history_row_sha256(row, wallet="mallory")


def test_binding_for_another_wallet_cannot_find_row_in_this_wallet_history():
    item = closeout()
    row = payment()
    wrong_binding = bind(item, row, wallet="mallory")
    with pytest.raises(RevenueSettlementEvidenceError, match="bound history row is absent"):
        reconcile([item], [row], [wrong_binding], wallet="alice")


def test_binding_unknown_closeout_item_is_rejected():
    item = closeout()
    row = payment()
    unknown = {
        "repo": "Sponsor/project",
        "pr": 99,
        "history_sha256s": [history_row_sha256(row, wallet="alice")],
    }
    with pytest.raises(RevenueSettlementInputError, match="unknown closeout item"):
        reconcile([item], [row], [unknown])


def test_cash_status_must_start_not_inferred():
    item = closeout()
    item["cash_status"] = "paid"
    with pytest.raises(RevenueSettlementInputError, match="must be not_inferred"):
        reconcile([item], [], [])


def test_fingerprint_is_key_order_independent_and_mutation_sensitive():
    row = payment()
    reordered = {key: row[key] for key in reversed(list(row))}
    assert history_row_sha256(row, wallet="alice") == history_row_sha256(reordered, wallet="alice")
    changed = dict(row)
    changed["amount"] = 9
    assert history_row_sha256(row, wallet="alice") != history_row_sha256(changed, wallet="alice")


def test_summary_counts_only_evidence_backed_amounts():
    first = closeout(pr=7, amount="10")
    second = closeout(pr=8, amount="8")
    third = closeout(pr=9, amount="3")
    paid = payment(amount=10, tag="1")
    partial = payment(amount=2.5, tag="2")
    results = reconcile([first, second, third], [paid, partial], [bind(first, paid), bind(second, partial)])
    summary = summarize_cash(results)
    assert summary == {
        "currency": "RTC",
        "verified_cash_total": "12.5",
        "partial_cash_total": "2.5",
        "fully_paid_items": 1,
        "partially_paid_items": 1,
        "unverified_items": 1,
        "cash_claim": "wallet_history_evidence_only",
    }


def test_boolean_amount_is_rejected():
    item = closeout(amount=True)
    with pytest.raises(RevenueSettlementInputError, match="decimal"):
        reconcile([item], [], [])


def test_non_finite_amount_is_rejected():
    item = closeout(amount="NaN")
    with pytest.raises(RevenueSettlementInputError, match="bounded positive decimal"):
        reconcile([item], [], [])


def test_history_source_is_strict():
    with pytest.raises(RevenueSettlementInputError, match="history source provenance"):
        inventory_history([], "alice", history_source="mystery")


def test_normalized_offline_capture_requires_exact_wallet_and_source():
    payload = {
        "schema_version": 1,
        "source": "rustchain_wallet_history",
        "wallet": "alice",
        "items": [payment()],
    }
    items, wallet = rs._history_capture(payload, wallet="alice")
    assert items == payload["items"]
    assert wallet == "alice"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"schema_version": 1, "wallet": "alice", "items": []}, "source was invalid"),
        ({"schema_version": 1, "source": "rustchain_wallet_history", "wallet": "mallory", "items": []}, "does not match"),
        ({"schema_version": 1, "items": []}, "source was invalid"),
    ],
)
def test_offline_capture_refuses_free_wallet_reinterpretation(payload, message):
    with pytest.raises(RevenueSettlementInputError, match=message):
        rs._history_capture(payload, wallet="alice")


def test_saved_canonical_provider_envelope_preserves_miner_wallet():
    row = payment()
    payload = {"ok": True, "miner_id": "alice", "transactions": [row], "total": 1}
    items, wallet = rs._history_capture(payload, wallet="alice")
    assert items == [row]
    assert wallet == "alice"


def test_saved_canonical_provider_envelope_rejects_other_miner():
    payload = {"ok": True, "miner_id": "mallory", "transactions": [payment()], "total": 1}
    with pytest.raises(RevenueSettlementInputError, match="wallet metadata was invalid"):
        rs._history_capture(payload, wallet="alice")


def test_saved_canonical_provider_envelope_rejects_incomplete_total():
    payload = {"ok": True, "miner_id": "alice", "transactions": [payment()], "total": 0}
    with pytest.raises(RevenueSettlementInputError, match="wallet metadata was invalid"):
        rs._history_capture(payload, wallet="alice")


def test_cli_online_path_accepts_canonical_transfer_in_without_to(tmp_path, monkeypatch, capsys):
    item = closeout()
    row = payment()
    closeout_path = tmp_path / "closeout.json"
    binding_path = tmp_path / "bindings.json"
    closeout_path.write_text(json.dumps({"schema_version": 1, "items": [item]}), encoding="utf-8")
    binding_path.write_text(json.dumps({"schema_version": 1, "items": [bind(item, row)]}), encoding="utf-8")
    monkeypatch.setattr(rs, "_query_canonical_history", lambda wallet: ([row], wallet))
    assert rs.main([str(closeout_path), str(binding_path), "--wallet", "alice", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["history_source"] == "queried_wallet"
    assert payload["items"][0]["cash_status"] == "verified_paid"


def test_cli_offline_path_rejects_wallet_mismatch_before_reconciliation(tmp_path, capsys):
    item = closeout()
    row = payment()
    closeout_path = tmp_path / "closeout.json"
    binding_path = tmp_path / "bindings.json"
    history_path = tmp_path / "history.json"
    closeout_path.write_text(json.dumps({"schema_version": 1, "items": [item]}))
    binding_path.write_text(json.dumps({"schema_version": 1, "items": [bind(item, row)]}))
    history_path.write_text(json.dumps({"schema_version": 1, "source": "rustchain_wallet_history", "wallet": "mallory", "items": [row]}))
    with pytest.raises(SystemExit) as exc:
        rs.main([str(closeout_path), str(binding_path), "--wallet", "alice", "--history", str(history_path)])
    assert exc.value.code == 2
    assert "does not match settlement wallet" in capsys.readouterr().err


def test_sub_unit_overpayment_beyond_default_decimal_precision_fails_closed():
    item = closeout(amount="100000000000000000000000000000")
    huge = payment(amount="100000000000000000000000000000", tag="1")
    tiny = payment(amount="0.000000000000000001", tag="2")
    with pytest.raises(RevenueSettlementEvidenceError, match="exceed"):
        reconcile([item], [huge, tiny], [bind(item, huge, tiny)])


def test_summary_preserves_sub_unit_cash_beyond_default_decimal_precision():
    summary = summarize_cash([
        {"currency": "RTC", "verified_amount": "100000000000000000000000000000", "cash_status": "verified_paid"},
        {"currency": "RTC", "verified_amount": "0.000000000000000001", "cash_status": "partially_verified"},
    ])
    assert summary["verified_cash_total"] == "100000000000000000000000000000.000000000000000001"
    assert summary["partial_cash_total"] == "0.000000000000000001"


@pytest.mark.parametrize("bad_type", [" transfer_in", "transfer_in ", "TRANSFER_IN", "transfer_in\n"])
def test_canonical_transfer_type_must_match_provider_contract_exactly(bad_type):
    item = closeout()
    row = payment()
    row["type"] = bad_type
    with pytest.raises(RevenueSettlementEvidenceError):
        reconcile([item], [row], [bind(item, row)])


@pytest.mark.parametrize("bad_status", [" confirmed", "confirmed ", "CONFIRMED", "confirmed\n"])
def test_canonical_status_must_match_provider_contract_exactly(bad_status):
    item = closeout()
    row = payment(status=bad_status)
    with pytest.raises(RevenueSettlementEvidenceError):
        reconcile([item], [row], [bind(item, row)])


def test_sender_identity_cannot_bypass_self_funding_with_whitespace():
    item = closeout()
    row = payment(sender="alice ")
    with pytest.raises(RevenueSettlementEvidenceError, match="malformed sender"):
        reconcile([item], [row], [bind(item, row)])


class _HistoryResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise rs.requests.HTTPError("bad status")

    def json(self):
        return self._payload


def test_online_reader_preserves_canonical_wallet_and_paginates(monkeypatch):
    rows = [payment(amount=1, tag=str(i)) for i in range(3)]
    calls = []

    def fake_get(url, *, params, timeout, verify):
        calls.append((url, dict(params), timeout, verify))
        offset = params["offset"]
        page = rows[offset : offset + 2]
        return _HistoryResponse({"ok": True, "miner_id": "alice", "transactions": page, "total": 3})

    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", "/ca.pem"))
    monkeypatch.setattr(rs.requests, "get", fake_get)
    monkeypatch.setattr(rs, "_MAX_ITEMS", 10)
    got, wallet = rs._query_canonical_history("alice")
    assert got == rows
    assert wallet == "alice"
    assert [call[1]["offset"] for call in calls] == [0, 2]
    assert all(call[1]["limit"] == 200 for call in calls)
    assert all(call[3] == "/ca.pem" for call in calls)


def test_online_reader_rejects_provider_wallet_mismatch(monkeypatch):
    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: _HistoryResponse({"ok": True, "miner_id": "mallory", "transactions": [], "total": 0}))
    with pytest.raises(rs.PayoutLookupError, match="malformed"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_legacy_wrapper_instead_of_inventing_provenance(monkeypatch):
    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: _HistoryResponse({"history": [legacy_payment()]}))
    with pytest.raises(rs.PayoutLookupError, match="malformed"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_total_drift_between_pages(monkeypatch):
    row1 = payment(amount=1, tag="1")
    row2 = payment(amount=1, tag="2")
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _HistoryResponse({"ok": True, "miner_id": "alice", "transactions": [row1], "total": 2})
        return _HistoryResponse({"ok": True, "miner_id": "alice", "transactions": [row2], "total": 3})

    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", fake_get)
    with pytest.raises(rs.PayoutLookupError, match="pagination changed"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_empty_page_before_total(monkeypatch):
    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: _HistoryResponse({"ok": True, "miner_id": "alice", "transactions": [], "total": 1}))
    with pytest.raises(rs.PayoutLookupError, match="incomplete"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_duplicate_rows_across_pages(monkeypatch):
    row = payment(amount=1, tag="1")
    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: _HistoryResponse({"ok": True, "miner_id": "alice", "transactions": [row], "total": 2}))
    with pytest.raises(rs.PayoutLookupError, match="duplicate rows"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_non_json_response(monkeypatch):
    class BadJSON(_HistoryResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: BadJSON({}))
    with pytest.raises(rs.PayoutLookupError, match="not valid JSON"):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_http_failure(monkeypatch):
    monkeypatch.setattr(rs, "_node_request_settings", lambda node_url: ("https://node", True))
    monkeypatch.setattr(rs.requests, "get", lambda *args, **kwargs: _HistoryResponse({}, status=503))
    with pytest.raises(rs.PayoutLookupError, match="request failed"):
        rs._query_canonical_history("alice")
