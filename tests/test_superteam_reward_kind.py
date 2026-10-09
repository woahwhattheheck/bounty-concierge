"""Focused guard for marketplace headline cash-vs-operative reward claims."""
import unittest

from concierge.superteam_provider import classify_operative_reward_kind


class SuperteamRewardKindTest(unittest.TestCase):
    def test_explicit_noncash_reward_overrides_advertised_usdc(self):
        result = classify_operative_reward_kind(
            "5,500 USDC Total Prizes\n## Reward\nWinners receive a "
            "Breakpoint ticket in place of the prize amount shown. "
            "No cash prize will be awarded.",
            None,
        )
        self.assertEqual(result["classification"], "PRUNE_NONCASH")
        self.assertFalse(result["cash_dispatch_authorized"])

    def test_headline_only_never_becomes_verified_cash(self):
        result = classify_operative_reward_kind(
            "Win 1,000 USDC from our open hackathon!", "",
        )
        self.assertEqual(result["classification"], "HOLD_CASH_UNVERIFIED")
        self.assertFalse(result["cash_payout_confirmed"])
        self.assertFalse(result["cash_dispatch_authorized"])


if __name__ == "__main__":
    unittest.main()
