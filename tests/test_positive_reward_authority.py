# SPDX-License-Identifier: MIT
"""Predecessor-killing regressions for positive, exact paid-work authority."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, getcontext, setcontext
from unittest import mock

import pytest

from concierge import active_claim_portfolio as acp
from concierge import bounty_qualification as bq


NOW = datetime(2026, 9, 15, 1, 0, 0, tzinfo=timezone.utc)


def _audit():
    return {
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }


def _qualification(amount: str, currency: str = "USD"):
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "reason_codes": [],
        "qualification": {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "signals": {
                "advertised_reward_usd": [amount] if currency == "USD" else [],
                "live_label_reward_usd": [],
                "advertised_reward_rtc": [amount] if currency == "RTC" else [],
                "live_label_reward_rtc": [],
                "canonical_audit_complete": True,
            },
        },
        "provenance": {"disposition": "ACTIONABLE", "dispatch": True, "signals": {}},
    }


def _availability():
    return {
        "schema": "bounty-availability/v1",
        "repo": "acme/widget",
        "number": 17,
        "disposition": "CLEAR",
        "dispatch": True,
        "reason_code": None,
        "issue_state": "open",
        "signal_codes": [],
        "evidence": [],
        "authority": {"effect": "new_work_dispatch_only"},
    }


def _candidate(reward_minor: int, currency: str = "USD"):
    return {
        "repo": "Acme/Widget",
        "number": 17,
        "sponsor_key": "sponsor-a",
        "worker_id": "worker-a",
        "reward_currency": currency,
        "reward_minor": reward_minor,
    }


def _policy():
    return {
        "version": "policy-v2",
        "max_active_claims_total": 4,
        "max_active_claims_per_worker": 2,
        "max_active_claims_per_sponsor": 2,
        "sponsor_overrides": {},
        "max_claim_age_seconds": 3600,
    }


def _live_result(reward_minor: int, amount: str, currency: str = "USD"):
    with (
        mock.patch.object(acp, "_utc_now", return_value=NOW),
        mock.patch.object(
            acp, "_qualify_live_authority", return_value=_qualification(amount, currency)
        ),
        mock.patch.object(acp, "_inspect_live_availability", return_value=_availability()),
    ):
        return acp.compile_live_active_claim_portfolio(
            [_candidate(reward_minor, currency)], [], _policy()
        )


def _row(receipt):
    assert len(receipt["results"]) == 1
    return receipt["results"][0]


@pytest.mark.parametrize(
    "snapshot",
    [
        {"body": "/bounty $0", "labels": ["$0"], "canonical_audit": _audit()},
        {"title": "Bounty $0", "labels": [], "canonical_audit": _audit()},
        {"body": "work", "labels": ["$0"], "canonical_audit": _audit()},
        {"body": "reward: 0 RTC", "labels": [], "canonical_audit": _audit()},
        {"title": "Bounty 0 RTC", "labels": ["bounty"], "canonical_audit": _audit()},
        {"body": "work", "labels": ["bounty", "0 RTC"], "canonical_audit": _audit()},
    ],
)
def test_zero_value_reward_never_dispatches(snapshot):
    result = bq.qualify_dispatch(snapshot)
    assert result["disposition"] == "HOLD"
    assert result["dispatch"] is False
    assert "ZERO_VALUE_REWARD" in result["reason_codes"]


@pytest.mark.parametrize(
    "snapshot",
    [
        {"body": "/bounty $0.01", "labels": ["$0.01"], "canonical_audit": _audit()},
        {"body": "reward: 1 RTC", "labels": [], "canonical_audit": _audit()},
    ],
)
def test_positive_reward_control_remains_actionable(snapshot):
    result = bq.qualify_dispatch(snapshot)
    assert result["disposition"] == "ACTIONABLE"
    assert result["dispatch"] is True
    assert "ZERO_VALUE_REWARD" not in result["reason_codes"]


def test_low_decimal_precision_cannot_round_9001_cents_to_9000():
    saved = getcontext().copy()
    try:
        getcontext().prec = 2
        getcontext().rounding = ROUND_DOWN
        receipt = _live_result(9000, "90.01")
    finally:
        setcontext(saved)

    row = _row(receipt)
    assert row["disposition"] == "CONFLICT_HOLD"
    assert "LIVE_REWARD_AMOUNT_MISMATCH" in row["reason_codes"]


def test_low_decimal_precision_preserves_exact_9012_cents():
    saved = getcontext().copy()
    try:
        getcontext().prec = 2
        getcontext().rounding = ROUND_DOWN
        receipt = _live_result(9012, "90.12")
    finally:
        setcontext(saved)

    assert _row(receipt)["disposition"] == "READY_FOR_INTERNAL_CLAIM"


def test_zero_actionable_signal_still_fails_closed_at_custody_boundary():
    receipt = _live_result(0, "0")
    row = _row(receipt)
    assert row["disposition"] == "CONFLICT_HOLD"
    assert "LIVE_REWARD_ZERO_VALUE" in row["reason_codes"]


def test_fractional_rtc_remains_unrepresentable_without_decimal_context():
    saved = getcontext().copy()
    try:
        getcontext().prec = 1
        getcontext().rounding = ROUND_DOWN
        receipt = _live_result(25, "25.5", "RTC")
    finally:
        setcontext(saved)

    row = _row(receipt)
    assert row["disposition"] == "CONFLICT_HOLD"
    assert "LIVE_REWARD_AMOUNT_UNREPRESENTABLE" in row["reason_codes"]


def test_pathological_positive_exponent_fails_before_large_integer_allocation():
    canonical, code = acp._canonical_live_reward_minor(_qualification("1E+999999"))
    assert canonical is None
    assert code == "LIVE_REWARD_AMOUNT_UNREPRESENTABLE"
