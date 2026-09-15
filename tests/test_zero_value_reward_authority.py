# SPDX-License-Identifier: MIT
"""Regression coverage for the positive paid-reward authority boundary."""

from __future__ import annotations

import unittest

from concierge.bounty_qualification import qualify_dispatch
from concierge.revenue_intake import qualify_revenue_intake


ISSUE_URL = "https://github.com/acme/widgets/issues/17"


def _audit():
    return {
        "repo": "acme/widgets",
        "number": 17,
        "issue_url": ISSUE_URL,
        "issue_state": "open",
        "open_pr_count": 0,
        "stale_listing_signal": False,
        "search_truncated": False,
    }


def _snapshot(**changes):
    value = {
        "listing_url": ISSUE_URL,
        "reward_evidence_urls": [ISSUE_URL],
        "body": "",
        "labels": [],
        "canonical_audit": _audit(),
    }
    value.update(changes)
    return value


class ZeroValueRewardAuthorityTests(unittest.TestCase):
    def assert_zero_hold(self, snapshot):
        result = qualify_dispatch(snapshot)
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("ZERO_VALUE_REWARD", result["reason_codes"])
        return result

    def test_zero_usd_body_is_hold(self):
        result = self.assert_zero_hold(_snapshot(body="/bounty $0"))
        self.assertEqual(result["signals"]["advertised_reward_usd"], ["0"])

    def test_zero_usd_title_is_hold(self):
        result = self.assert_zero_hold(_snapshot(title="Bounty $0"))
        self.assertEqual(result["signals"]["advertised_reward_usd"], ["0"])

    def test_zero_usd_live_label_is_hold(self):
        result = self.assert_zero_hold(_snapshot(labels=["$0"]))
        self.assertEqual(result["signals"]["live_label_reward_usd"], ["0"])

    def test_zero_rtc_body_is_hold(self):
        result = self.assert_zero_hold(_snapshot(body="reward: 0 RTC"))
        self.assertEqual(result["signals"]["advertised_reward_rtc"], ["0"])
        self.assertEqual(result["signals"]["rtc_reward_source"], "body")

    def test_zero_rtc_title_is_hold(self):
        result = self.assert_zero_hold(
            _snapshot(title="Bounty 0 RTC", labels=["bounty"])
        )
        self.assertEqual(result["signals"]["advertised_reward_rtc"], ["0"])
        self.assertEqual(result["signals"]["rtc_reward_source"], "title")

    def test_zero_rtc_live_label_is_hold(self):
        result = self.assert_zero_hold(_snapshot(labels=["bounty", "0 RTC"]))
        self.assertEqual(result["signals"]["live_label_reward_rtc"], ["0"])
        self.assertEqual(result["signals"]["rtc_reward_source"], "label")

    def test_zero_and_positive_evidence_keeps_zero_and_ambiguity_reasons(self):
        result = self.assert_zero_hold(
            _snapshot(body="/bounty $0\nreward: $10")
        )
        self.assertIn("AMBIGUOUS_ADVERTISED_REWARD", result["reason_codes"])
        self.assertEqual(result["signals"]["advertised_reward_usd"], ["0", "10"])

    def test_zero_mismatch_keeps_both_authority_reasons(self):
        result = self.assert_zero_hold(
            _snapshot(body="/bounty $10", labels=["$0"])
        )
        self.assertIn("REWARD_MISMATCH", result["reason_codes"])
        self.assertEqual(result["signals"]["advertised_reward_usd"], ["10"])
        self.assertEqual(result["signals"]["live_label_reward_usd"], ["0"])

    def test_positive_usd_control_remains_actionable(self):
        result = qualify_dispatch(_snapshot(body="/bounty $0.01", labels=["$0.01"]))
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertTrue(result["dispatch"])
        self.assertNotIn("ZERO_VALUE_REWARD", result["reason_codes"])

    def test_positive_rtc_control_remains_actionable(self):
        result = qualify_dispatch(_snapshot(body="reward: 1 RTC"))
        self.assertEqual(result["disposition"], "ACTIONABLE")
        self.assertTrue(result["dispatch"])
        self.assertNotIn("ZERO_VALUE_REWARD", result["reason_codes"])

    def test_revenue_intake_cannot_reauthorize_zero_reward(self):
        result = qualify_revenue_intake(_snapshot(body="/bounty $0"))
        self.assertEqual(result["disposition"], "HOLD")
        self.assertFalse(result["dispatch"])
        self.assertIn("QUALIFICATION:ZERO_VALUE_REWARD", result["reason_codes"])
        self.assertEqual(
            result["qualification"]["signals"]["advertised_reward_usd"], ["0"]
        )

    def test_zero_reason_is_operator_safe_and_does_not_echo_source_body(self):
        secret = "DO_NOT_ECHO_THIS_ZERO_SOURCE"
        result = qualify_revenue_intake(
            _snapshot(body=f"{secret}\n/bounty $0")
        )
        rendered = repr(result)
        self.assertNotIn(secret, rendered)
        self.assertIn("QUALIFICATION:ZERO_VALUE_REWARD", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
