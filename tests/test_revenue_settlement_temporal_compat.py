# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from concierge import revenue_settlement as rs


NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def closeout(*, merged_at="2026-09-13T10:00:00Z"):
    row = {
        "repo": "Sponsor/project",
        "pr": 7,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": "10",
        "cash_status": "not_inferred",
    }
    if merged_at is not ...:
        row["merged_at"] = merged_at
    return row


def payment(*, timestamp="2026-09-13T10:00:00Z"):
    return {
        "type": "transfer_in",
        "amount": "10",
        "from": "treasury",
        "timestamp": timestamp,
        "tx_hash": "tx-compat",
    }


def bind(item, row):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [rs.history_row_sha256(row, wallet="alice")],
    }


def test_transfer_exactly_at_merge_boundary_is_valid(monkeypatch):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: NOW)
    item = closeout()
    row = payment()
    result = rs.reconcile_cash(
        [item],
        [row],
        [bind(item, row)],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert result[0]["verified_amount"] == "10"


def test_unbound_merge_without_merged_at_remains_not_inferred(monkeypatch):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: NOW)
    item = closeout(merged_at=...)
    row = payment()
    result = rs.reconcile_cash(
        [item],
        [row],
        [],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "not_inferred"
    assert result[0]["verified_amount"] == "0"


def test_future_merge_is_not_cash_authority(monkeypatch):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: NOW)
    item = closeout(merged_at="2026-09-13T12:00:01Z")
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(
        rs.RevenueSettlementEvidenceError,
        match="merge timestamp is in the future",
    ):
        rs.reconcile_cash(
            [item],
            [row],
            [bind(item, row)],
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
        )
