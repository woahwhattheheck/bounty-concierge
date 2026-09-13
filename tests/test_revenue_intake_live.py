from __future__ import annotations

import unittest
from unittest.mock import patch, sentinel

from concierge.revenue_intake import (
    RevenueIntakeInputError,
    main,
    qualify_live_revenue_intake,
    qualify_revenue_intake,
)


ISSUE_URL = "https://github.com/acme/widgets/issues/17"


def gate_result(
    disposition: str = "ACTIONABLE",
    *,
    dispatch: bool | None = None,
    code: str | None = None,
) -> dict:
    if dispatch is None:
        dispatch = disposition == "ACTIONABLE"
    reasons = []
    if code is not None:
        severity = "REJECT" if disposition == "REJECT" else "HOLD"
        reasons.append(
            {
                "code": code,
                "severity": severity,
                "message": "safe reason",
            }
        )
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "reason_codes": [code] if code else [],
        "reasons": reasons,
        "signals": {"live": True},
    }


def live_preflight(
    disposition: str = "ACTIONABLE",
    *,
    dispatch: bool | None = None,
    code: str | None = None,
) -> dict:
    return {
        "repo": "acme/widgets",
        "number": 17,
        "attempt_count": 0,
        "attempt_signal_count": 0,
        "comments_truncated": False,
        "canonical_audit": {
            "repo": "acme/widgets",
            "number": 17,
            "issue_url": ISSUE_URL,
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
        "qualification": gate_result(
            disposition,
            dispatch=dispatch,
            code=code,
        ),
    }


class LiveRevenueIntakeTests(unittest.TestCase):
    @patch("concierge.revenue_intake.preflight_bounty")
    def test_live_mode_refreshes_preflight_before_provenance(self, mocked):
        mocked.return_value = live_preflight()

        result = qualify_live_revenue_intake(
            "acme/widgets",
            17,
            listing_url="https://mirror.example/bounties/widgets-17",
            session=sentinel.session,
            max_pages=3,
            saturation_threshold=6,
        )

        mocked.assert_called_once_with(
            "acme/widgets",
            17,
            None,
            max_pages=3,
            saturation_threshold=6,
            session=sentinel.session,
        )
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertTrue(result["dispatch"])
        self.assertEqual(result["canonical_source_url"], ISSUE_URL)
        self.assertEqual(
            result["provenance"]["signals"]["listing_relation"],
            "MIRROR_CANONICALIZED",
        )

    @patch("concierge.revenue_intake.preflight_bounty")
    def test_live_reward_hold_cannot_be_overridden_by_issue_url(self, mocked):
        mocked.return_value = live_preflight(
            "HOLD",
            code="REWARD_NOT_ADVERTISED",
        )

        result = qualify_live_revenue_intake("acme/widgets", 17)

        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn(
            "QUALIFICATION:REWARD_NOT_ADVERTISED",
            result["reason_codes"],
        )
        self.assertTrue(result["provenance"]["dispatch"])

    @patch("concierge.revenue_intake.preflight_bounty")
    def test_explicit_empty_listing_url_fails_closed(self, mocked):
        mocked.return_value = live_preflight()

        with self.assertRaisesRegex(
            ValueError,
            "listing_url must be non-empty",
        ):
            qualify_live_revenue_intake(
                "acme/widgets",
                17,
                listing_url="",
            )

    @patch("concierge.revenue_intake.preflight_bounty")
    def test_live_mode_fails_closed_without_canonical_issue_url(self, mocked):
        value = live_preflight()
        value["canonical_audit"]["issue_url"] = None
        mocked.return_value = value

        with self.assertRaisesRegex(
            RevenueIntakeInputError,
            "missing canonical issue URL",
        ):
            qualify_live_revenue_intake("acme/widgets", 17)

    @patch("concierge.revenue_intake.preflight_bounty")
    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_snapshot_mode_remains_offline_and_never_runs_preflight(
        self, mocked_qualification, mocked_preflight
    ):
        mocked_qualification.return_value = gate_result()
        snapshot = {
            "listing_url": ISSUE_URL,
            "reward_evidence_urls": [ISSUE_URL],
            "canonical_audit": live_preflight()["canonical_audit"],
        }

        result = qualify_revenue_intake(snapshot)

        self.assertTrue(result["dispatch"])
        mocked_preflight.assert_not_called()

    def test_cli_rejects_mixed_snapshot_and_live_authority(self):
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "snapshot.json",
                    "--repo",
                    "acme/widgets",
                    "--issue",
                    "17",
                ]
            )

        self.assertEqual(raised.exception.code, 2)

    @patch("concierge.revenue_intake.qualify_live_revenue_intake")
    def test_cli_live_mode_routes_without_snapshot_file(self, mocked):
        mocked.return_value = {
            "disposition": "ACTIONABLE",
            "dispatch": True,
            "canonical_source_url": ISSUE_URL,
            "reason_codes": [],
            "reasons": [],
        }

        code = main(
            [
                "--repo",
                "acme/widgets",
                "--issue",
                "17",
                "--listing-url",
                "https://mirror.example/bounties/widgets-17",
                "--max-pages",
                "2",
                "--saturation-threshold",
                "5",
            ]
        )

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            "acme/widgets",
            17,
            listing_url="https://mirror.example/bounties/widgets-17",
            max_pages=2,
            saturation_threshold=5,
        )

    @patch("concierge.revenue_intake.qualify_live_revenue_intake")
    def test_cli_never_exits_success_for_non_dispatchable_actionable_result(
        self, mocked
    ):
        mocked.return_value = {
            "disposition": "ACTIONABLE",
            "dispatch": False,
            "canonical_source_url": ISSUE_URL,
            "reason_codes": [],
            "reasons": [],
        }

        code = main(["--repo", "acme/widgets", "--issue", "17"])

        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
