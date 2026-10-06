# SPDX-License-Identifier: MIT
"""Focused terminal-phrase coverage for the bounty availability guard."""

from concierge.bounty_availability import _terminal_signals


CAP_CLOSED = ("MAINTAINER_CAP_CLOSED_SIGNAL",)


def test_declining_new_bounty_attempts_is_terminal():
    text = "We are not going to be accepting new bounty attempts for this issue."
    assert _terminal_signals(text) == CAP_CLOSED


def test_no_longer_accepting_new_bounty_attempts_is_terminal():
    assert _terminal_signals("We are no longer accepting new bounty attempts.") == CAP_CLOSED


def test_positive_accepting_language_is_not_terminal():
    assert _terminal_signals("We are accepting new bounty attempts.") == ()


def test_negated_stop_language_remains_non_terminal():
    assert _terminal_signals("We are not stopping new submissions.") == ()
