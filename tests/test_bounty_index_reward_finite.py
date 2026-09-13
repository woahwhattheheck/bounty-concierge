# SPDX-License-Identifier: MIT
"""Regression coverage for finite bounty reward parsing."""

import math

from concierge.bounty_index import parse_reward


def test_overflowing_reward_text_does_not_return_infinity():
    reward = parse_reward(f"{'9' * 400} RTC bounty", "")

    assert reward == 0.0
    assert math.isfinite(reward)


def test_overflowing_title_reward_falls_through_to_valid_body_reward():
    reward = parse_reward(
        f"{'9' * 400} RTC bounty",
        "The maintained payout is 25 RTC.",
    )

    assert reward == 25.0


def test_overflowing_reward_falls_through_to_later_finite_reward_in_same_field():
    reward = parse_reward(
        f"{'9' * 400} RTC placeholder; payout 25 RTC",
        "",
    )

    assert reward == 25.0


def test_large_finite_and_canonical_grouped_rewards_remain_supported():
    large = parse_reward(f"1{'0' * 307} RTC", "")
    grouped = parse_reward("Reward: 1,234.5 RTC", "")

    assert math.isfinite(large)
    assert large > 0
    assert grouped == 1234.5
