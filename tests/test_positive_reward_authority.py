# SPDX-License-Identifier: MIT
"""Predecessor-killing regressions for positive, exact paid-work authority."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, getcontext, setcontext
import unittest
from unittest import mock

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
    with mock.patch.object(acp, "_utc_now", return_value=NOW):
        with mock.patch.object(
            acp, "_qualify_live_authority", return_value=_qualification(amount, currency)
        ):
            with mock.patch.object(
                acp, "_inspect_live_availability", return_value=_availability()
            ):
                return acp.compile_live_active_claim_portfolio(
                    [_candidate(reward_minor, currency)], [], _policy()
                )


def _row(receipt):
    if len(receipt["results"]) != 1:
        raise AssertionError("expected exactly one result row")
    return receipt["results"][0]


class PositiveRewardAuthorityTests(unittest.TestCase):
    def test_zero_value_reward_never_dispatches(self):
        snapshots = [
            {"body": "/bounty $0", "labels": ["$0"], "canonical_audit": _audit()},
            {"title": "Bounty $0", "labels": [], "canonical_audit": _audit()},
            {"body": "work", "labels": ["$0"], "canonical_audit": _audit()},
            {"body": "reward: 0 RTC", "labels": [], "canonical_audit": _audit()},
            {"title": "Bounty 0 RTC", "labels": ["bounty"], "canonical_audit": _audit()},
            {"body": "work", "labels": ["bounty", "0 RTC"], "canonical_audit": _audit()},
        ]
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot):
                result = bq.qualify_dispatch(snapshot)
                self.assertEqual(result["disposition"], "HOLD")
                self.assertFalse(result["dispatch"])
                self.assertIn("ZERO_VALUE_REWARD", result["reason_codes"])

    def test_positive_reward_control_remains_actionable(self):
        snapshots = [
            {"body": "/bounty $0.01", "labels": ["$0.01"], "canonical_audit": _audit()},
            {"body": "reward: 1 RTC", "labels": [], "canonical_audit": _audit()},
        ]
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot):
                result = bq.qualify_dispatch(snapshot)
                self.assertEqual(result["disposition"], "ACTIONABLE")
                self.assertTrue(result["dispatch"])
                self.assertNotIn("ZERO_VALUE_REWARD", result["reason_codes"])

    def test_low_decimal_precision_cannot_round_9001_cents_to_9000(self):
        saved = getcontext().copy()
        try:
            getcontext().prec = 2
            getcontext().rounding = ROUND_DOWN
            receipt = _live_result(9000, "90.01")
        finally:
            setcontext(saved)

        row = _row(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_AMOUNT_MISMATCH", row["reason_codes"])

    def test_low_decimal_precision_preserves_exact_9012_cents(self):
        saved = getcontext().copy()
        try:
            getcontext().prec = 2
            getcontext().rounding = ROUND_DOWN
            receipt = _live_result(9012, "90.12")
        finally:
            setcontext(saved)

        self.assertEqual(_row(receipt)["disposition"], "READY_FOR_INTERNAL_CLAIM")

    def test_zero_actionable_signal_still_fails_closed_at_custody_boundary(self):
        receipt = _live_result(0, "0")
        row = _row(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_ZERO_VALUE", row["reason_codes"])

    def test_fractional_rtc_remains_unrepresentable_without_decimal_context(self):
        saved = getcontext().copy()
        try:
            getcontext().prec = 1
            getcontext().rounding = ROUND_DOWN
            receipt = _live_result(25, "25.5", "RTC")
        finally:
            setcontext(saved)

        row = _row(receipt)
        self.assertEqual(row["disposition"], "CONFLICT_HOLD")
        self.assertIn("LIVE_REWARD_AMOUNT_UNREPRESENTABLE", row["reason_codes"])

    def test_pathological_positive_exponent_fails_before_large_integer_allocation(self):
        canonical, code = acp._canonical_live_reward_minor(_qualification("1E+999999"))
        self.assertIsNone(canonical)
        self.assertEqual(code, "LIVE_REWARD_AMOUNT_UNREPRESENTABLE")


if __name__ == "__main__":
    unittest.main()
