from __future__ import annotations

import unittest
from unittest.mock import patch

from concierge.revenue_intake import qualify_revenue_intake


def provenance_snapshot():
    issue_url = "https://github.com/acme/widgets/issues/17"
    return {
        "listing_url": issue_url,
        "reward_evidence_urls": [issue_url],
        "body": "SECRET ISSUE BODY MUST NOT LEAK",
        "comments": ["SECRET COMMENT MUST NOT LEAK"],
        "canonical_audit": {
            "repo": "acme/widgets",
            "number": 17,
            "issue_url": issue_url,
            "issue_state": "open",
            "open_pr_count": 0,
            "stale_listing_signal": False,
            "search_truncated": False,
        },
    }


def qualification(disposition="ACTIONABLE", *, dispatch=None, code=None):
    if dispatch is None:
        dispatch = disposition == "ACTIONABLE"
    reasons = []
    codes = []
    if code:
        severity = "REJECT" if disposition == "REJECT" else "HOLD"
        reasons = [{"code": code, "severity": severity, "message": "safe reason"}]
        codes = [code]
    return {
        "disposition": disposition,
        "dispatch": dispatch,
        "reason_codes": codes,
        "reasons": reasons,
        "signals": {"open_pr_count": 0},
    }


class RevenueIntakeTests(unittest.TestCase):
    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_both_gates_must_authorize_dispatch(self, mocked):
        mocked.return_value = qualification()
        result = qualify_revenue_intake(provenance_snapshot())
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertTrue(result["dispatch"])
        self.assertEqual(
            result["canonical_source_url"],
            "https://github.com/acme/widgets/issues/17",
        )

    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_qualification_hold_dominates(self, mocked):
        mocked.return_value = qualification("HOLD", code="SATURATED_COMPETITION")
        result = qualify_revenue_intake(provenance_snapshot())
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn(
            "QUALIFICATION:SATURATED_COMPETITION",
            result["reason_codes"],
        )

    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_provenance_hold_dominates(self, mocked):
        mocked.return_value = qualification()
        value = provenance_snapshot()
        value["reward_evidence_urls"] = ["https://mirror.test/reward"]
        result = qualify_revenue_intake(value)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn(
            "PROVENANCE:CANONICAL_REWARD_EVIDENCE_MISSING",
            result["reason_codes"],
        )

    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_reject_dominates_hold(self, mocked):
        mocked.return_value = qualification(
            "REJECT", code="PRIVATE_CONTEXT_REQUIRED"
        )
        value = provenance_snapshot()
        value["reward_evidence_urls"] = []
        result = qualify_revenue_intake(value)
        self.assertEqual(result["disposition"], "REJECT")
        self.assertFalse(result["dispatch"])

    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_safe_result_never_echoes_raw_source_text(self, mocked):
        mocked.return_value = qualification()
        result = qualify_revenue_intake(provenance_snapshot())
        rendered = repr(result)
        self.assertNotIn("SECRET ISSUE BODY", rendered)
        self.assertNotIn("SECRET COMMENT", rendered)

    @patch("concierge.revenue_intake.qualify_dispatch")
    def test_saturation_threshold_is_forwarded_exactly(self, mocked):
        mocked.return_value = qualification()
        qualify_revenue_intake(provenance_snapshot(), saturation_threshold=7)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["saturation_threshold"], 7)


if __name__ == "__main__":
    unittest.main()
