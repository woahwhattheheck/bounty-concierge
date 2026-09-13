# SPDX-License-Identifier: MIT
"""Regression coverage for non-finite RTC reward parsing."""

import math

from concierge import bounty_index


def test_oversized_rtc_amount_does_not_become_infinite_reward():
    oversized = "9" * 400

    reward = bounty_index.parse_reward(f"[Bounty: {oversized} RTC] overflow", "")

    assert reward == 0.0
    assert math.isfinite(reward)


def test_invalid_oversized_title_amount_falls_through_to_valid_body_reward():
    oversized = "9" * 400

    reward = bounty_index.parse_reward(
        f"[Bounty: {oversized} RTC] overflow",
        "Canonical payout: 25 RTC",
    )

    assert reward == 25.0
    assert math.isfinite(reward)


def test_large_finite_rewards_remain_supported():
    reward = bounty_index.parse_reward("[Bounty: 1,000,000 RTC] major task", "")

    assert reward == 1_000_000.0
    assert math.isfinite(reward)
