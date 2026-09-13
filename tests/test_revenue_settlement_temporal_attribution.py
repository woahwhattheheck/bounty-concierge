# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from concierge import revenue_settlement as rs


WALLET = "alice"
NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def closeout(*, merged_at="2026-09-13T10:00:00Z", pr=7, amount="10"):
    return {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
        "merged_at": merged_at,
    }


def payment(
    *,
    timestamp="2026-09-13T10:00:01Z",
    amount="10",
    tx_hash="tx-a",
    status=None,
):
    row = {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "timestamp": timestamp,
        "tx_hash": tx_hash,
    }
    if status is not None:
        row["status"] = status
    return row


def bind(item, *rows):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet=WALLET) for row in rows
        ],
    }


def reconcile(item, rows):
    return rs.reconcile_cash(
        [item],
        rows,
        [bind(item, *rows)],
        wallet=WALLET,
        history_wallet=WALLET,
        history_source="captured_wallet",
    )


@pytest.fixture(autouse=True)
def fixed_verifier_time(monkeypatch):
    monkeypatch.setattr(rs, "_trusted_now", lambda: NOW)


def test_status_omitted_old_transfer_cannot_settle_later_merge():
    item = closeout(merged_at="2026-09-13T10:00:00Z")
    old = payment(timestamp="2026-09-12T10:00:00Z", status=None)

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="bound transfer predates the merged work",
    ):
        reconcile(item, [old])


def test_bound_incoming_without_time_is_not_cash_evidence():
    item = closeout()
    row = payment()
    del row["timestamp"]

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="omitted transfer initiation timestamp",
    ):
        reconcile(item, [row])

    inventory = rs.inventory_history(
        [row],
        WALLET,
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is False
    assert "transfer initiation timestamp" in inventory[0]["reason"]


def test_conflicting_timestamp_aliases_fail_closed():
    item = closeout()
    row = payment()
    row["created_at"] = "2026-09-13T10:00:02Z"

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="conflicting transfer initiation timestamps",
    ):
        reconcile(item, [row])


def test_future_transfer_cannot_be_verified():
    item = closeout()
    row = payment(timestamp="2026-09-13T12:00:01Z")

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="initiation timestamp is in the future",
    ):
        reconcile(item, [row])


def test_future_merge_cannot_be_verified():
    item = closeout(merged_at="2026-09-13T12:00:01Z")
    row = payment(timestamp="2026-09-13T12:00:01Z")

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merge timestamp is in the future",
    ):
        reconcile(item, [row])


def test_transfer_exactly_at_merge_boundary_is_allowed():
    item = closeout(merged_at="2026-09-13T10:00:00Z")
    row = payment(timestamp="2026-09-13T10:00:00Z")

    result = reconcile(item, [row])
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_equal_timestamp_aliases_are_allowed():
    item = closeout()
    row = payment()
    row["created_at"] = row["timestamp"]

    result = reconcile(item, [row])
    assert result[0]["cash_status"] == "verified_paid"


def test_noncanonical_closeout_merge_time_fails_closed():
    item = closeout(merged_at="2026-09-13T10:00:00+00:00")
    row = payment()

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merged_at must be canonical UTC",
    ):
        reconcile(item, [row])


def test_noncanonical_transfer_time_fails_closed():
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:01+00:00")

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transfer timestamp must be canonical UTC",
    ):
        reconcile(item, [row])


def test_unbound_merge_still_makes_no_cash_claim_without_merged_at():
    item = closeout()
    item.pop("merged_at")
    row = payment()

    result = rs.reconcile_cash(
        [item],
        [row],
        [],
        wallet=WALLET,
        history_wallet=WALLET,
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_bound_merge_requires_authoritative_merged_at():
    item = closeout()
    item.pop("merged_at")
    row = payment()

    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merged_at must be canonical UTC",
    ):
        reconcile(item, [row])


def test_unix_seconds_are_supported_for_canonical_history_rows():
    item = closeout(merged_at="1970-01-01T00:00:00Z")
    row = payment(timestamp=1)

    result = reconcile(item, [row])
    assert result[0]["cash_status"] == "verified_paid"
