# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from concierge import revenue_settlement as rs


NOW = datetime(2026, 9, 13, 0, 1, 0, tzinfo=timezone.utc)
MISSING = object()


def closeout(*, merged_at="2026-09-13T00:00:10Z"):
    row = {
        "repo": "Sponsor/project",
        "pr": 7,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": "10",
        "cash_status": "not_inferred",
    }
    if merged_at is not MISSING:
        row["merged_at"] = merged_at
    return row


def payment(
    *,
    timestamp="2026-09-13T00:00:11Z",
    status=MISSING,
):
    row = {
        "type": "transfer_in",
        "amount": 10,
        "from": "treasury",
        "tx_hash": "tx-temporal",
    }
    if timestamp is not MISSING:
        row["timestamp"] = timestamp
    if status is not MISSING:
        row["status"] = status
    return row


def bind(item, *rows):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet="alice") for row in rows
        ],
    }


def reconcile(item, row, *, selected=True):
    bindings = [bind(item, row)] if selected else []
    return rs.reconcile_cash(
        [item],
        [row],
        bindings,
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )


@pytest.fixture(autouse=True)
def verifier_clock(monkeypatch):
    monkeypatch.setattr(rs, "_verification_now", lambda: NOW)


def test_valid_post_merge_status_omitted_transfer_is_accepted():
    item = closeout()
    row = payment()
    result = reconcile(item, row)
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_transfer_exactly_at_merge_boundary_is_accepted():
    item = closeout()
    row = payment(timestamp=item["merged_at"])
    result = reconcile(item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_confirmed_transfer_before_merge_is_rejected():
    item = closeout()
    row = payment(timestamp="2026-09-13T00:00:09Z", status="confirmed")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="predates the merged work",
    ):
        reconcile(item, row)


@pytest.mark.parametrize("status", [MISSING, "confirmed"])
def test_selected_transfer_without_time_is_rejected_even_if_status_is_confirmed(status):
    item = closeout()
    row = payment(timestamp=MISSING, status=status)
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="omitted transfer timestamp",
    ):
        reconcile(item, row)


def test_transfer_after_verifier_clock_is_rejected():
    item = closeout()
    row = payment(timestamp="2026-09-13T00:01:01Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="transfer timestamp is in the future",
    ):
        reconcile(item, row)


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        True,
        -1,
        253402300800,
        "2026-09-13T00:00:11+00:00",
        "2026-09-13T00:00:11.1234567Z",
        "2026-02-30T00:00:11Z",
    ],
)
def test_malformed_or_out_of_range_transfer_time_is_rejected(bad_timestamp):
    item = closeout()
    row = payment(timestamp=bad_timestamp)
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="timestamp"):
        reconcile(item, row)


def test_conflicting_timestamp_aliases_are_rejected():
    item = closeout()
    row = payment()
    row["created_at"] = "2026-09-13T00:00:12Z"
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="conflicting transfer timestamps",
    ):
        reconcile(item, row)


def test_matching_timestamp_aliases_are_accepted():
    item = closeout()
    row = payment()
    row["created_at"] = row["timestamp"]
    result = reconcile(item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_bound_settlement_requires_closeout_merge_time():
    item = closeout(merged_at=MISSING)
    row = payment()
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="requires closeout merged_at",
    ):
        reconcile(item, row)


@pytest.mark.parametrize(
    "bad_merged_at",
    [
        "2026-09-13T00:00:10+00:00",
        "2026-09-13T00:00:10.1234567Z",
        "2026-02-30T00:00:10Z",
        1789257610,
        True,
    ],
)
def test_closeout_merge_time_must_be_canonical_utc_text(bad_merged_at):
    item = closeout(merged_at=bad_merged_at)
    row = payment()
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merged_at",
    ):
        reconcile(item, row)


def test_future_closeout_merge_time_is_rejected():
    item = closeout(merged_at="2026-09-13T00:01:01Z")
    row = payment(timestamp="2026-09-13T00:01:01Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merge timestamp is in the future",
    ):
        reconcile(item, row)


def test_integer_unix_seconds_are_supported_when_after_merge():
    item = closeout(merged_at="1970-01-01T00:00:00Z")
    row = payment(timestamp=1)
    result = reconcile(item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_unbound_merge_does_not_require_temporal_evidence():
    item = closeout(merged_at=MISSING)
    row = payment(timestamp=MISSING)
    result = reconcile(item, row, selected=False)
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_inventory_marks_missing_time_incoming_row_unbindable():
    row = payment(timestamp=MISSING)
    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is False
    assert "omitted transfer timestamp" in inventory[0]["reason"]


def test_inventory_marks_valid_time_incoming_row_bindable():
    row = payment()
    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is True
    assert inventory[0]["timestamp"] == "2026-09-13T00:00:11Z"


def test_verifier_clock_must_be_timezone_aware(monkeypatch):
    monkeypatch.setattr(
        rs,
        "_verification_now",
        lambda: datetime(2026, 9, 13, 0, 1, 0),
    )
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="timezone-aware",
    ):
        reconcile(closeout(), payment())
