# SPDX-License-Identifier: MIT
from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest

from concierge import revenue_settlement as rs


MERGED_AT = "2026-09-13T10:00:00Z"
NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def closeout(*, merged_at=MERGED_AT, pr=7, amount="10"):
    row = {
        "repo": "Sponsor/project",
        "pr": pr,
        "canonical_url": f"https://github.com/Sponsor/project/pull/{pr}",
        "head_sha": "a" * 40,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "next_action": "monitor_settlement",
        "reason": "merged_followup_already_routed",
        "settlement_followup_url": "https://example.test/settlement",
        "cash_status": "not_inferred",
    }
    if merged_at is not ...:
        row["merged_at"] = merged_at
    return row


def minimal_closeout(*, pr=7, amount="10"):
    return {
        "repo": "Sponsor/project",
        "pr": pr,
        "state": "MERGED",
        "currency": "RTC",
        "advertised_amount": amount,
        "cash_status": "not_inferred",
    }


def payment(
    *,
    timestamp="2026-09-13T10:00:01Z",
    status=None,
    amount=10,
    tx_hash="tx-temporal",
):
    row = {
        "type": "transfer_in",
        "amount": amount,
        "from": "treasury",
        "tx_hash": tx_hash,
    }
    if timestamp is not ...:
        row["timestamp"] = timestamp
    if status is not None:
        row["status"] = status
    return row


def legacy_payment(*, created_at="2026-09-13T10:00:01Z", amount=10):
    row = {
        "amount_rtc": amount,
        "from": "treasury",
        "to": "alice",
        "tx_hash": "legacy-temporal",
    }
    if created_at is not ...:
        row["created_at"] = created_at
    return row


def bind(item, *rows):
    return {
        "repo": item["repo"],
        "pr": item["pr"],
        "history_sha256s": [
            rs.history_row_sha256(row, wallet="alice") for row in rows
        ],
    }


def reconcile(monkeypatch, item, *rows):
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: NOW)
    return rs.reconcile_cash(
        [item],
        list(rows),
        [bind(item, *rows)],
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
    row = payment(status="confirmed", timestamp=...)
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted transfer timestamp"):
        reconcile(monkeypatch, item, row)


def test_status_omitted_row_without_provider_time_fails_closed(monkeypatch):
    item = closeout()
    row = payment(status=None, timestamp=...)
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted transfer timestamp"):
        reconcile(monkeypatch, item, row)


def test_bound_transfer_cannot_predate_merge(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T09:59:59Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="predates merge"):
        reconcile(monkeypatch, item, row)


def test_status_omitted_transfer_cannot_predate_merge(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T09:59:59Z", status=None)
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="predates merge"):
        reconcile(monkeypatch, item, row)


def test_bound_transfer_cannot_be_from_future(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="timestamp is in the future"):
        reconcile(monkeypatch, item, row)


def test_future_merge_is_rejected(monkeypatch):
    item = closeout(merged_at="2026-09-13T12:00:01Z")
    row = payment(timestamp="2026-09-13T12:00:01Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="merge timestamp is in the future"):
        reconcile(monkeypatch, item, row)


def test_exact_merge_boundary_is_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp=MERGED_AT, status=None)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_exact_verifier_now_boundary_is_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T12:00:00Z")
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_microsecond_post_merge_transfer_is_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:00.000001Z")
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_equal_timestamp_and_created_at_are_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:02Z")
    row["created_at"] = "2026-09-13T10:00:02Z"
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_equivalent_text_and_unix_timestamp_aliases_are_accepted(monkeypatch):
    item = closeout()
    row = payment(timestamp=1789293602)
    row["created_at"] = "2026-09-13T10:00:02Z"
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_conflicting_provider_timestamp_aliases_fail_closed(monkeypatch):
    item = closeout()
    row = payment(timestamp="2026-09-13T10:00:01Z")
    row["created_at"] = "2026-09-13T10:00:02Z"
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="conflicting transfer timestamps"):
        reconcile(monkeypatch, item, row)


def test_provider_unix_seconds_are_accepted(monkeypatch):
    item = closeout(merged_at="1970-01-01T00:00:00Z")
    row = payment(timestamp=1)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


@pytest.mark.parametrize("bad_time", [True, -1, 253402300800, 1.5])
def test_unsafe_provider_time_shapes_fail_closed(monkeypatch, bad_time):
    item = closeout()
    row = payment(timestamp=bad_time)
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="timestamp"):
        reconcile(monkeypatch, item, row)


def test_bound_producer_cash_requires_merge_timestamp(monkeypatch):
    item = closeout(merged_at=...)
    row = payment()
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted merged_at"):
        reconcile(monkeypatch, item, row)


@pytest.mark.parametrize(
    "merged_at",
    [
        "2026-09-13T10:00:00+00:00",
        "2026-09-13 10:00:00Z",
        "2026-09-13T10:00:00.1234567Z",
        1789293600,
    ],
)
def test_merge_timestamp_must_be_canonical_utc_text(monkeypatch, merged_at):
    item = closeout(merged_at=merged_at)
    row = payment()
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="canonical UTC"):
        reconcile(monkeypatch, item, row)


def test_partial_payment_semantics_survive_temporal_fence(monkeypatch):
    item = closeout(amount="10")
    row = payment(amount=4)
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "partially_verified"
    assert result[0]["verified_amount"] == "4"


def test_legacy_explicit_recipient_row_gets_same_production_temporal_gate(monkeypatch):
    item = closeout()
    row = legacy_payment()
    result = reconcile(monkeypatch, item, row)
    assert result[0]["cash_status"] == "verified_paid"


def test_legacy_explicit_recipient_premerge_row_is_rejected(monkeypatch):
    item = closeout()
    row = legacy_payment(created_at="2026-09-13T09:59:59Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="predates merge"):
        reconcile(monkeypatch, item, row)


def test_trusted_clock_is_sampled_once_for_multiple_bound_rows(monkeypatch):
    item = closeout(amount="10")
    first = payment(amount=4, timestamp="2026-09-13T10:00:01Z", tx_hash="tx-1")
    second = payment(amount=6, timestamp="2026-09-13T10:00:02Z", tx_hash="tx-2")
    calls = 0

    def trusted_now():
        nonlocal calls
        calls += 1
        return NOW

    monkeypatch.setattr(rs, "_trusted_utc_now", trusted_now)
    result = rs.reconcile_cash(
        [item],
        [first, second],
        [bind(item, first, second)],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "verified_paid"
    assert calls == 1


def test_unbound_producer_closeout_does_not_sample_clock_or_require_merge_time(monkeypatch):
    item = closeout(merged_at=...)
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


def test_any_producer_marker_activates_strict_merge_requirement(monkeypatch):
    item = minimal_closeout()
    item["head_sha"] = "a" * 40
    row = payment()
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="omitted merged_at"):
        reconcile(monkeypatch, item, row)


def test_explicit_merged_at_alone_activates_temporal_contract(monkeypatch):
    item = minimal_closeout()
    item["merged_at"] = MERGED_AT
    row = payment(timestamp="2026-09-13T09:59:59Z")
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="predates merge"):
        reconcile(monkeypatch, item, row)


def test_historical_minimal_low_level_rows_preserve_fixture_contract(monkeypatch):
    item = minimal_closeout()
    row = payment(timestamp="fixture-only-noncanonical")
    monkeypatch.setattr(
        rs,
        "_trusted_utc_now",
        lambda: (_ for _ in ()).throw(AssertionError("clock should not be sampled")),
    )
    result = rs.reconcile_cash(
        [item],
        [row],
        [bind(item, row)],
        wallet="alice",
        history_wallet="alice",
        history_source="captured_wallet",
    )
    assert result[0]["cash_status"] == "verified_paid"


def test_production_api_exposes_no_caller_as_of_escape_hatch():
    assert "as_of" not in inspect.signature(rs.reconcile_cash).parameters


def test_naive_trusted_clock_fails_closed(monkeypatch):
    item = closeout()
    row = payment()
    monkeypatch.setattr(rs, "_trusted_utc_now", lambda: datetime(2026, 9, 13, 12, 0, 0))
    with pytest.raises(rs.RevenueSettlementEvidenceError, match="timezone-aware UTC"):
        rs.reconcile_cash(
            [item],
            [row],
            [bind(item, row)],
            wallet="alice",
            history_wallet="alice",
            history_source="captured_wallet",
        )
