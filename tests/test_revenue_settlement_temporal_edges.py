# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import revenue_settlement as rs


MERGED_AT = "2026-09-13T10:00:00Z"
TRUSTED_NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def closeout(*, amount="10", merged_at=MERGED_AT):
    row = {
        "repo": "Sponsor/project",
        "pr": 7,
        "canonical_url": "https://github.com/Sponsor/project/pull/7",
        "head_sha": "a" * 40,
        "advertised_amount": amount,
        "currency": "RTC",
        "state": "MERGED",
        "next_action": "monitor_settlement",
        "reason": "merged_followup_already_routed",
        "settlement_followup_url": "https://example.test/settlement/7",
        "cash_status": "not_inferred",
    }
    if merged_at is not None:
        row["merged_at"] = merged_at
    return row


def payment(*, amount=10, timestamp="2026-09-13T10:00:01Z", status="confirmed"):
    row = {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "tx_hash": "tx-temporal-1",
    }
    if timestamp is not None:
        row["timestamp"] = timestamp
    if status is not None:
        row["status"] = status
    return row


def binding(item, row):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet="alice")
        ],
    }


def reconcile(monkeypatch, item, row):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: TRUSTED_NOW)
    return rs.reconcile_cash(
        [item],
        [row],
        [binding(item, row)],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )


def test_confirmed_transfer_before_merge_is_rejected(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T09:59:59Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="predates merge",
    ):
        reconcile(monkeypatch, item, row)


@pytest.mark.parametrize("status", ["confirmed", None])
def test_bound_transfer_without_time_is_rejected(monkeypatch, status):
    item = closeout()
    row = payment(timestamp=None, status=status)
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="omitted transfer timestamp",
    ):
        reconcile(monkeypatch, item, row)


def test_future_transfer_is_rejected(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="timestamp is in the future",
    ):
        reconcile(monkeypatch, item, row)


def test_future_merge_is_rejected(monkeypatch):
    item = closeout(merged_at="2026-09-13T12:00:01Z")
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merge timestamp is in the future",
    ):
        reconcile(monkeypatch, item, row)


def test_exact_merge_boundary_is_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp=MERGED_AT, status=None)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_post_merge_pre_now_transfer_is_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T11:59:59.123456Z")
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_equal_timestamp_and_created_at_are_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:02Z")
    row["created_at"] = "2026-09-13T10:00:02Z"
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_conflicting_timestamp_fields_are_rejected(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:02Z")
    row["created_at"] = "2026-09-13T10:00:03Z"
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="conflicting transfer timestamps",
    ):
        reconcile(monkeypatch, item, row)


def test_exact_unix_seconds_are_accepted(monkeypatch):
    item = closeout()
    unix_seconds = int(
        datetime(2026, 9, 13, 10, 0, 2, tzinfo=timezone.utc).timestamp()
    )
    row = payment(timestamp=unix_seconds)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


@pytest.mark.parametrize("bad_time", [True, -1, 253402300800, 1.5])
def test_unsafe_history_time_shapes_fail_closed(monkeypatch, bad_time):
    item = closeout()
    row = payment(timestamp=bad_time)
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="timestamp",
    ):
        reconcile(monkeypatch, item, row)


def test_producer_shaped_bound_closeout_requires_merged_at(monkeypatch):
    item = closeout(merged_at=None)
    row = payment()
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="omitted merged_at",
    ):
        reconcile(monkeypatch, item, row)


def test_partial_payment_semantics_survive_temporal_fence(monkeypatch):
    item = closeout(amount="10")
    row = payment(amount="4")
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "partially_verified"
    assert result[0]["verified_amount"] == "4"


def test_trusted_clock_is_sampled_once_for_multiple_bound_rows(monkeypatch):
    item = closeout(amount="10")
    first = payment(amount="4", timestamp="2026-09-13T10:00:01Z")
    second = payment(amount="6", timestamp="2026-09-13T10:00:02Z")
    second["tx_hash"] = "tx-temporal-2"
    calls = 0

    def trusted_now():
        nonlocal calls
        calls += 1
        return TRUSTED_NOW

    monkeypatch.setattr(rs, "_trusted_utc_now", trusted_now)
    result = rs.reconcile_cash(
        [item],
        [first, second],
        [{
            "repo": item["repo"],
            "pr": item["pr"],
            "history_sha256s": [
                rs.history_row_sha256(first, wallet="alice"),
                rs.history_row_sha256(second, wallet="alice"),
            ],
        }],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert calls == 1


def test_unbound_producer_closeout_does_not_require_time(monkeypatch):
    item = closeout(merged_at=None)
    monkeypatch.setattr(
        rs,
        "_trusted_utc_now",
        lambda: (_ for _ in ()).throw(AssertionError("clock should not be sampled")),
    )
    result = rs.reconcile_cash(
        [item],
        [],
        [],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["reason"] == "no_payment_evidence_bound"
