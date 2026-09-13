from __future__ import annotations

import unittest
from unittest.mock import patch, sentinel

from concierge.revenue_dispatch import (
    RevenueDispatchError,
    qualify_available_live_revenue_intake,
)


def intake(*, dispatch=True, disposition="ACTIONABLE"):
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "canonical_source_url": "https://github.com/acme/widgets/issues/17",
        "reason_codes": [],
        "reasons": [],
        "qualification": {"disposition": disposition, "dispatch": dispatch, "signals": {}},
        "provenance": {"disposition": "ACTIONABLE", "dispatch": True, "signals": {}},
    }


def availability(*, dispatch=True, reason=None):
    return {
        "schema": "bounty-availability/v1",
        "repo": "acme/widgets",
        "number": 17,
        "disposition": "CLEAR" if dispatch else "HOLD",
        "dispatch": dispatch,
        "reason_code": reason,
        "issue_state": "open",
        "signal_codes": (
            [] if dispatch else ["MAINTAINER_ACCEPTANCE_SIGNAL"]
        ),
        "evidence": [],
        "authority": {
            "effect": "new_work_dispatch_only",
            "terminal_signal_is_payout_proof": False,
            "terminal_signal_is_revenue_proof": False,
            "raw_comment_text_retained": False,
            "user_identity_retained": False,
        },
    }


class RevenueDispatchTests(unittest.TestCase):
    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_both_live_gates_clear_authorizes_new_work(self, intake_mock, avail_mock):
        intake_mock.return_value = intake()
        avail_mock.return_value = availability()
        result = qualify_available_live_revenue_intake(
            "acme/widgets",
            17,
            listing_url="https://mirror.example/item",
            token="TOKEN",
            session=sentinel.session,
            max_pages=3,
            saturation_threshold=2,
        )
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["disposition"], "ACTIONABLE")
        intake_mock.assert_called_once_with(
            "acme/widgets",
            17,
            listing_url="https://mirror.example/item",
            token="TOKEN",
            session=sentinel.session,
            max_pages=3,
            saturation_threshold=2,
        )
        avail_mock.assert_called_once_with(
            "acme/widgets",
            17,
            "TOKEN",
            session=sentinel.session,
            max_pages=3,
        )

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_terminal_outcome_overrides_open_actionable_intake(
        self, intake_mock, avail_mock
    ):
        intake_mock.return_value = intake()
        avail_mock.return_value = availability(
            dispatch=False,
            reason="MAINTAINER_TERMINAL_OUTCOME",
        )
        result = qualify_available_live_revenue_intake("acme/widgets", 17)
        self.assertFalse(result["dispatch"])
        self.assertEqual(result["disposition"], "HOLD")
        self.assertIn(
            "AVAILABILITY:MAINTAINER_TERMINAL_OUTCOME",
            result["reason_codes"],
        )
        self.assertFalse(
            result["dispatch_authority"]["new_work_dispatch"]
        )

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_upstream_hold_skips_extra_availability_provider_reads(
        self, intake_mock, avail_mock
    ):
        value = intake(dispatch=False, disposition="HOLD")
        value["reason_codes"] = ["QUALIFICATION:SATURATED_COMPETITION"]
        intake_mock.return_value = value
        result = qualify_available_live_revenue_intake("acme/widgets", 17)
        avail_mock.assert_not_called()
        self.assertFalse(result["dispatch"])
        self.assertEqual(
            result["availability"]["reason_code"],
            "UPSTREAM_INTAKE_NOT_DISPATCHABLE",
        )

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_reject_is_not_downgraded_to_hold(self, intake_mock, avail_mock):
        intake_mock.return_value = intake(dispatch=True, disposition="REJECT")
        avail_mock.return_value = availability(
            dispatch=False,
            reason="MAINTAINER_TERMINAL_OUTCOME",
        )
        result = qualify_available_live_revenue_intake("acme/widgets", 17)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertFalse(result["dispatch"])

    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_malformed_intake_dispatch_fails_closed(self, intake_mock):
        value = intake()
        value["dispatch"] = 1
        intake_mock.return_value = value
        with self.assertRaises(RevenueDispatchError):
            qualify_available_live_revenue_intake("acme/widgets", 17)

    @patch("concierge.revenue_dispatch.inspect_bounty_availability")
    @patch("concierge.revenue_dispatch.qualify_live_revenue_intake")
    def test_non_dispatch_availability_requires_reason(
        self, intake_mock, avail_mock
    ):
        intake_mock.return_value = intake()
        avail_mock.return_value = availability(dispatch=False, reason=None)
        with self.assertRaises(RevenueDispatchError):
            qualify_available_live_revenue_intake("acme/widgets", 17)


if __name__ == "__main__":
    unittest.main()
