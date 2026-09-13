# SPDX-License-Identifier: MIT
"""Regression coverage for malformed top-level skill tag keys."""

from concierge.skill_matcher import _normalise_tags


def test_non_string_and_blank_skill_names_are_ignored():
    assert _normalise_tags(
        {
            7: {"aliases": ["seven"]},
            "": ["empty"],
            "   ": {"bounty_labels": ["blank"]},
            "python": {"aliases": ["Py"], "bounty_labels": ["backend"]},
        }
    ) == {"python": ["python", "py", "backend"]}
