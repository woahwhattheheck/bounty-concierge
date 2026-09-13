from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.revenue_intake import (
    RevenueIntakeInputError,
    qualify_live_revenue_intake,
    qualify_revenue_intake,
)


ISSUE_URL = "https://github.com/acme/widgets/issues/17"


def _qualification(disposition: str = "HOLD") -> dict:
    return {
        "disposition": disposition,
        "dispatch": disposition == "ACTIONABLE",
        "reason_codes": [],
        "reasons": [],
        "signals": {},
    }


def _preflight(disposition: str = "HOLD") -> dict:
    return {
        "canonical_audit": {
            "repo": "acme/widgets",
            "number": 17,
            "issue_url": ISSUE_URL,
            "issue_state": "open",
            "open_pr_count": 20,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
        "qualification": _qualification(disposition),
    }


def _provenance() -> dict:
    return {
        "disposition": "ACTIONABLE",
        "dispatch": True,
        "reason_codes": [],
        "reasons": [],
        "signals": {},
        "use_source_url": ISSUE_URL,
    }


class LiveSaturationPolicyTests(unittest.TestCase):
    @patch("concierge.revenue_intake.verify_source_provenance")
    @patch("concierge.revenue_intake.preflight_bounty")
    def test_live_caller_cannot_loosen_authoritative_threshold(
        self, mocked_preflight, mocked_provenance
    ):
        mocked_preflight.return_value = _preflight("HOLD")
        mocked_provenance.return_value = _provenance()

        result = qualify_live_revenue_intake(
            "acme/widgets",
            17,
            saturation_threshold=999,
        )

        mocked_preflight.assert_called_once_with(
            "acme/widgets",
            17,
            None,
            max_pages=10,
            saturation_threshold=4,
        )
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])

    @patch("concierge.revenue_intake.verify_source_provenance")
    @patch("concierge.revenue_intake.preflight_bounty")
    def test_live_caller_may_tighten_threshold(
        self, mocked_preflight, mocked_provenance
    ):
        mocked_preflight.return_value = _preflight("HOLD")
        mocked_provenance.return_value = _provenance()

        qualify_live_revenue_intake(
            "acme/widgets",
            17,
            saturation_threshold=2,
        )

        mocked_preflight.assert_called_once_with(
            "acme/widgets",
            17,
            None,
            max_pages=10,
            saturation_threshold=2,
        )

    @patch("concierge.revenue_intake.preflight_bounty")
    def test_invalid_live_threshold_fails_before_network_preflight(self, mocked_preflight):
        for value in (0, -1, True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    RevenueIntakeInputError,
                    "live saturation_threshold must be a positive integer",
                ):
                    qualify_live_revenue_intake(
                        "acme/widgets",
                        17,
                        saturation_threshold=value,
                    )
        mocked_preflight.assert_not_called()

    @patch("concierge.revenue_intake.verify_source_provenance")
    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_trusted_snapshot_replay_remains_parameterized(
        self, mocked_qualification, mocked_provenance
    ):
        mocked_qualification.return_value = _qualification("ACTIONABLE")
        mocked_provenance.return_value = _provenance()
        snapshot = {"trusted": True}

        result = qualify_revenue_intake(snapshot, saturation_threshold=999)

        mocked_qualification.assert_called_once_with(
            snapshot,
            saturation_threshold=999,
        )
        self.assertTrue(result["dispatch"])


if __name__ == "__main__":
    unittest.main()
