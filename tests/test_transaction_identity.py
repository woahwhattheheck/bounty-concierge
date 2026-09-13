from __future__ import annotations

import copy

import pytest

from concierge import revenue_settlement as rs


def closeout(*, pr: int = 7, amount: str = "10") -> dict:
    return {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }


def payment(*, amount: str = "10", tx_hash: str = "tx-a", timestamp: int = 1) -> dict:
    return {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "timestamp": timestamp,
        "tx_hash": tx_hash,
    }


def legacy_payment(*, amount: str = "10", timestamp: int = 1, tx_hash=...):
    row = {
        "amount_rtc": amount,
        "from": "treasury",
        "to": "alice",
        "created_at": timestamp,
    }
    if tx_hash is not ...:
        row["tx_hash"] = tx_hash
    return row


def bind(item: dict, *rows: dict) -> dict:
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet="alice") for row in rows
        ],
    }


def reconcile(items: list[dict], history: list[dict], bindings: list[dict]):
    return rs.reconcile_cash(
        items,
        history,
        bindings,
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )


def test_same_canonical_transaction_with_timestamp_drift_cannot_double_count():
    item = closeout()
    first = payment(amount="5", tx_hash="same-transaction", timestamp=1)
    second = payment(amount="5", tx_hash="same-transaction", timestamp=2)

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transaction identity cannot be counted more than once",
    ):
        reconcile([item], [first, second], [bind(item, first, second)])


def test_same_canonical_transaction_with_extra_field_drift_cannot_double_count():
    item = closeout()
    first = payment(amount="5", tx_hash="same-transaction")
    second = copy.deepcopy(first)
    second["memo"] = "different-row-bytes"

    assert rs.history_row_sha256(first, wallet="alice") != rs.history_row_sha256(
        second, wallet="alice"
    )
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transaction identity cannot be counted more than once",
    ):
        reconcile([item], [first, second], [bind(item, first, second)])

    inventory = rs.inventory_history(
        [first, second],
        "alice",
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is True
    assert inventory[1] == {
        "history_sha256": rs.history_row_sha256(second, wallet="alice"),
        "history_wallet": "alice",
        "history_source": "captured_wallet",
        "timestamp": second["timestamp"],
        "bindable": False,
        "reason": "wallet transaction identity is duplicated in history",
    }


@pytest.mark.parametrize(
    "value",
    [None, "", " tx", "tx ", "tx hash", "tx\nhash", 7],
)
def test_canonical_incoming_requires_strict_transaction_identity(value):
    row = payment()
    row["tx_hash"] = value

    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )

    assert inventory[0]["bindable"] is False
    assert "transaction identity" in inventory[0]["reason"]


def test_canonical_incoming_requires_transaction_identity_field():
    row = payment()
    del row["tx_hash"]

    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )

    assert inventory[0]["bindable"] is False
    assert "omitted transaction identity" in inventory[0]["reason"]


def test_distinct_transaction_identities_can_settle_one_award():
    item = closeout()
    first = payment(amount="4", tx_hash="tx-a", timestamp=1)
    second = payment(amount="6", tx_hash="tx-b", timestamp=2)

    result = reconcile([item], [first, second], [bind(item, first, second)])

    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_legacy_rows_with_same_explicit_transaction_identity_cannot_double_count():
    item = closeout()
    first = legacy_payment(amount="5", timestamp=1, tx_hash="legacy-same")
    second = legacy_payment(amount="5", timestamp=2, tx_hash="legacy-same")

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transaction identity cannot be counted more than once",
    ):
        reconcile([item], [first, second], [bind(item, first, second)])


def test_legacy_row_without_transaction_identity_remains_compatible():
    item = closeout()
    row = legacy_payment(tx_hash=...)

    result = reconcile([item], [row], [bind(item, row)])

    assert result[0]["cash_status"] == "verified_paid"


def test_legacy_identity_reuse_across_closeout_items_is_rejected():
    first_item = closeout(pr=7, amount="5")
    second_item = closeout(pr=8, amount="5")
    first = legacy_payment(amount="5", timestamp=1, tx_hash="legacy-same")
    second = legacy_payment(amount="5", timestamp=2, tx_hash="legacy-same")

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transaction identity cannot be counted more than once",
    ):
        reconcile(
            [first_item, second_item],
            [first, second],
            [bind(first_item, first), bind(second_item, second)],
        )


def test_legacy_transaction_aliases_cannot_conflict():
    item = closeout()
    row = legacy_payment(tx_hash="tx-a")
    row["tx_id"] = "tx-b"

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="conflicting transaction identities",
    ):
        reconcile([item], [row], [bind(item, row)])


class HistoryResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_online_reader_rejects_same_transaction_across_changed_pages(monkeypatch):
    first = payment(amount="5", tx_hash="same-transaction", timestamp=1)
    second = payment(amount="5", tx_hash="same-transaction", timestamp=2)
    pages = iter(
        [
            {
                "ok": True,
                "miner_id": "alice",
                "transactions": [first],
                "total": 2,
            },
            {
                "ok": True,
                "miner_id": "alice",
                "transactions": [second],
                "total": 2,
            },
        ]
    )

    monkeypatch.setattr(
        rs,
        "_node_request_settings",
        lambda node_url: ("https://node", True),
    )
    monkeypatch.setattr(
        rs.requests,
        "get",
        lambda *args, **kwargs: HistoryResponse(next(pages)),
    )

    with pytest.raises(
        rs.PayoutLookupError,
        match="duplicate canonical incoming transaction identity",
    ):
        rs._query_canonical_history("alice")


def test_online_reader_rejects_missing_incoming_transaction_identity(monkeypatch):
    row = payment()
    del row["tx_hash"]
    payload = {
        "ok": True,
        "miner_id": "alice",
        "transactions": [row],
        "total": 1,
    }

    monkeypatch.setattr(
        rs,
        "_node_request_settings",
        lambda node_url: ("https://node", True),
    )
    monkeypatch.setattr(
        rs.requests,
        "get",
        lambda *args, **kwargs: HistoryResponse(payload),
    )

    with pytest.raises(
        rs.PayoutLookupError,
        match="malformed incoming transaction identity",
    ):
        rs._query_canonical_history("alice")
