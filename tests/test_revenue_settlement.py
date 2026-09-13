# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.revenue_settlement import (
    RevenueSettlementEvidenceError,
    RevenueSettlementInputError,
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


def payment(*, amount=10, to="alice", status=None, tag="a"):
    row = {
        "type": "transfer_in",
        "amount": amount,
        "to": to,
        "timestamp": f"2026-09-13T00:00:0{tag}Z",
        "tx": f"tx-{tag}",
    }
    if status is not None:
        row["status"] = status
    return row


def bind(item, *rows):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [history_row_sha256(row) for row in rows],
    }


def test_unbound_merge_does_not_become_cash():
    item = closeout()
    row = payment()
    result = reconcile_cash([item], [row], [], wallet="alice")
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_exact_bound_confirmed_payment_is_verified():
    item = closeout()
    row = payment()
    result = reconcile_cash([item], [row], [bind(item, row)], wallet="alice")
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"
    assert result[0]["payment_evidence"] == [
        {
            "history_sha256": history_row_sha256(row),
            "amount_rtc": "10",
            "status": "confirmed",
        }
    ]


def test_multiple_explicit_rows_can_sum_to_exact_award():
    item = closeout(amount="10")
    first = payment(amount="4.25", tag="1")
    second = payment(amount="5.75", tag="2")
    result = reconcile_cash(
        [item],
        [first, second],
        [bind(item, first, second)],
        wallet="alice",
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_partial_bound_payment_is_not_upgraded_to_paid():
    item = closeout(amount="10")
    row = payment(amount="4")
    result = reconcile_cash([item], [row], [bind(item, row)], wallet="alice")
    assert result[0]["cash_status"] == "partially_verified"
    assert result[0]["verified_amount"] == "4"


@pytest.mark.parametrize("status", ["pending", "confirming", "failed"])
def test_nonterminal_bound_payment_fails_closed(status):
    item = closeout()
    row = payment(status=status)
    with pytest.raises(RevenueSettlementEvidenceError, match="not confirmed"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_unknown_provider_status_fails_before_matching():
    item = closeout()
    row = payment(status="mystery")
    with pytest.raises(RevenueSettlementEvidenceError, match="unknown settlement status"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_bound_payment_to_other_wallet_fails_closed():
    item = closeout()
    row = payment(to="mallory")
    with pytest.raises(RevenueSettlementEvidenceError, match="another wallet"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_overpayment_is_not_silently_counted():
    item = closeout(amount="10")
    row = payment(amount="11")
    with pytest.raises(RevenueSettlementEvidenceError, match="exceed"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_non_rtc_closeout_cannot_use_rustchain_history():
    item = closeout(currency="USD")
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="not denominated in RTC"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_unmerged_item_cannot_have_payment_bound_as_closeout_cash():
    item = closeout(state="OPEN")
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="not merged"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_missing_bound_row_fails_closed():
    item = closeout()
    selected = payment(tag="1")
    actual = payment(tag="2")
    with pytest.raises(RevenueSettlementEvidenceError, match="is absent"):
        reconcile_cash([item], [actual], [bind(item, selected)], wallet="alice")


def test_same_history_row_cannot_pay_two_awards():
    first = closeout(pr=7)
    second = closeout(pr=8)
    row = payment()
    bindings = [bind(first, row), bind(second, row)]
    with pytest.raises(RevenueSettlementInputError, match="cannot settle multiple"):
        reconcile_cash([first, second], [row], bindings, wallet="alice")


def test_duplicate_indistinguishable_history_rows_are_rejected():
    row = payment()
    with pytest.raises(RevenueSettlementEvidenceError, match="duplicate indistinguishable"):
        inventory_history([row, copy.deepcopy(row)], "alice")


def test_conflicting_amount_fields_are_rejected_when_bound():
    item = closeout()
    row = payment()
    row["amount_rtc"] = 9
    with pytest.raises(RevenueSettlementEvidenceError, match="conflicting amount"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_conflicting_recipient_fields_are_rejected_when_bound():
    item = closeout()
    row = payment()
    row["to_addr"] = "mallory"
    with pytest.raises(RevenueSettlementEvidenceError, match="conflicting recipient"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_unrelated_reward_rows_do_not_block_bound_transfer_reconciliation():
    item = closeout()
    reward = {"type": "reward", "amount": 2, "timestamp": 1}
    row = payment()
    result = reconcile_cash([item], [reward, row], [bind(item, row)], wallet="alice")
    assert result[0]["cash_status"] == "verified_paid"


def test_inventory_marks_unbindable_rows_without_inventing_payment_semantics():
    reward = {"type": "reward", "amount": 2, "timestamp": 1}
    inventory = inventory_history([reward], "alice")
    assert inventory[0]["bindable"] is False
    assert inventory[0]["history_sha256"] == history_row_sha256(reward)


def test_bound_outgoing_transfer_is_rejected():
    item = closeout()
    row = payment()
    row["type"] = "transfer_out"
    with pytest.raises(RevenueSettlementEvidenceError, match="outgoing transfer"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


def test_bound_self_funded_transfer_is_rejected():
    item = closeout()
    row = payment()
    row["from"] = "alice"
    with pytest.raises(RevenueSettlementEvidenceError, match="self-funded"):
        reconcile_cash([item], [row], [bind(item, row)], wallet="alice")


@pytest.mark.parametrize("wallet", ["", " alice", "alice ", "alice bob", "alice\nbob"])
def test_wallet_identity_is_strict(wallet):
    with pytest.raises(RevenueSettlementInputError, match="wallet"):
        inventory_history([], wallet)


def test_binding_unknown_closeout_item_is_rejected():
    item = closeout()
    row = payment()
    unknown = {
        "repo": "Sponsor/project",
        "pr": 99,
        "history_sha256s": [history_row_sha256(row)],
    }
    with pytest.raises(RevenueSettlementInputError, match="unknown closeout item"):
        reconcile_cash([item], [row], [unknown], wallet="alice")


def test_cash_status_must_start_not_inferred():
    item = closeout()
    item["cash_status"] = "paid"
    with pytest.raises(RevenueSettlementInputError, match="must be not_inferred"):
        reconcile_cash([item], [], [], wallet="alice")


def test_fingerprint_is_key_order_independent_and_mutation_sensitive():
    row = payment()
    reordered = {key: row[key] for key in reversed(list(row))}
    assert history_row_sha256(row) == history_row_sha256(reordered)
    changed = dict(row)
    changed["amount"] = 9
    assert history_row_sha256(row) != history_row_sha256(changed)


def test_summary_counts_only_evidence_backed_amounts():
    first = closeout(pr=7, amount="10")
    second = closeout(pr=8, amount="8")
    third = closeout(pr=9, amount="3")
    paid = payment(amount=10, tag="1")
    partial = payment(amount=2.5, tag="2")
    results = reconcile_cash(
        [first, second, third],
        [paid, partial],
        [bind(first, paid), bind(second, partial)],
        wallet="alice",
    )
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
        reconcile_cash([item], [], [], wallet="alice")


def test_non_finite_amount_is_rejected():
    item = closeout(amount="NaN")
    with pytest.raises(RevenueSettlementInputError, match="bounded positive decimal"):
        reconcile_cash([item], [], [], wallet="alice")
