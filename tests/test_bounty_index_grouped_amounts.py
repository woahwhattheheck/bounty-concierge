# SPDX-License-Identifier: MIT
"""Focused regressions for grouped RTC reward parsing."""

from concierge import bounty_index


def test_parse_multiple_thousands_groups_without_tail_matching():
    assert bounty_index.parse_reward("[Bounty: 1,000,000 RTC] Huge task", "") == 1_000_000.0


def test_parse_grouped_decimal_amount():
    assert bounty_index.parse_reward("Reward: 1,234.5 RTC", "") == 1234.5


def test_reject_malformed_grouping_instead_of_matching_numeric_tail():
    assert bounty_index.parse_reward("Reward: 1,000,00 RTC", "") == 0.0
    assert bounty_index.parse_reward("Reward: 1,00 RTC", "") == 0.0
