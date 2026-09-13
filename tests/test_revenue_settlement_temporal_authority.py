# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from concierge import revenue_settlement as rs


NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def closeout(*, merged_at="2026-09-13T00:00:00Z", pr=7, amount="10"):
    row = {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }
    if merged_at is not ...:
        row["merged_at"] = merged_at
    return row


def payment(*, timestamp="2026-09-13T00:00:01Z", status=None, amount=10):
    row = {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "timestamp": timestamp,
        "tx_hash": "tx-temporal",
    }
    if status is not None:
        row["status"] = status
    return row


def legacy_payment(*, created_at="2026-09-13T00:00:01Z", amount=10):
    return {
        "amount_rtc": amount,
        "from": "treasury",
        "to": "alice",
        "created_at": created_at,
        "tx_hash": "legacy-temporal",
    }


def bind(item, *rows):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet="alice") for row in rows
        ],
    }


def reconcile(monkeypatch, item, row):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: NOW)
    return rs.reconcile_cash(
        [item],
        [row],
        [bind(item, row)],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )


def test_status_omitted_canonical_row_is_paid_only_with_valid_temporal_authority(monkeypatch):
    item = closeout()
    row = payment(status=None)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_confirmed_row_without_provider_time_fails_closed(monkeypatch):
    item = closeout()
    row = payment(status="confirmed")
    row.pop("timestamp")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted transfer timestamp"):
        reconcile(monkeypatch, item, row)


def test_status_omitted_row_without_provider_time_fails_closed(monkeypatch):
    item = closeout()
    row = payment(status=None)
    row.pop("timestamp")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted transfer timestamp"):
        reconcile(monkeypatch, item, row)


def test_bound_transfer_cannot_predate_merge(monkeypatch):
    item = closeout(merged_at="2026-09-13T00:00:02Z")
    row = payment(timestamp="2026-09-13T00:00:01Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="predates merged work"):
        reconcile(monkeypatch, item, row)


def test_bound_transfer_cannot_be_from_future(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="timestamp is in the future"):
        reconcile(monkeypatch, item, row)


def test_bound_cash_requires_merge_timestamp(monkeypatch):
    item = closeout(merged_at=...)
    row = payment()
    with pytest.raises(rs.RevenueSettlementInputError, match="requires merged_at"):
        reconcile(monkeypatch, item, row)


@pytest.mark.parametrize(
    "merged_at",
    [
        "2026-09-13T00:00:00+00:00",
        "2026-09-13 00:00:00Z",
        "2026-09-13T00:00:00.1234567Z",
        1789257600,
    ],
)
def test_merge_timestamp_must_be_canonical_utc_text(monkeypatch, merged_at):
    item = closeout(merged_at=merged_at)
    row = payment()
    with pytest.raises(rs.RevenueSettlementInputError, match="canonical UTC"):
        reconcile(monkeypatch, item, row)


def test_provider_unix_seconds_are_accepted(monkeypatch):
    item = closeout(merged_at="1970-01-01T00:00:00Z")
    row = payment(timestamp=1)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_conflicting_provider_timestamp_aliases_fail_closed(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T00:00:01Z")
    row["created_at"] = "2026-09-13T00:00:02Z"
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="conflicting transfer timestamps"):
        reconcile(monkeypatch, item, row)


def test_legacy_explicit_recipient_row_gets_same_temporal_gate(monkeypatch):
    item = closeout()
    row = legacy_payment()
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_inventory_marks_timeless_cash_row_unbindable():
    row = payment()
    row.pop("timestamp")
    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is False
    assert "omitted transfer timestamp" in inventory[0]["reason"]


def test_inventory_exposes_normalized_transfer_time_for_auditing():
    row = payment(timestamp=1)
    inventory = rs.inventory_history(
        [row],
        "alice",
        history_source="captured_wallet",
    )
    assert inventory[0]["bindable"] is True
    assert inventory[0]["transfer_at"] == "1970-01-01T00:00:01Z"
