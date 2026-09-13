# SPDX-License-Identifier: MIT
"""Regression coverage for explicit empty FAQ entry mappings."""

from concierge import faq_engine


def test_explicit_empty_entries_do_not_fall_back_to_defaults():
    """An explicit empty mapping must not silently select built-in FAQs."""
    assert faq_engine.fuzzy_match("what is rtc", entries={}) == ("", "", 0.0)
