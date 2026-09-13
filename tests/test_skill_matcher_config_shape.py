# SPDX-License-Identifier: MIT
"""Focused shape-resilience tests for skill tag configuration."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge.skill_matcher import _normalise_tags


def test_structured_null_optional_fields_fall_back_to_skill_name():
    assert _normalise_tags(
        {"python": {"aliases": None, "bounty_labels": None}}
    ) == {"python": ["python"]}


def test_structured_non_list_optional_fields_are_ignored():
    assert _normalise_tags(
        {"rust": {"aliases": "rs", "bounty_labels": {"backend": True}}}
    ) == {"rust": ["rust"]}


def test_structured_mixed_lists_ignore_non_string_and_empty_entries():
    assert _normalise_tags(
        {
            "python": {
                "aliases": ["Py", None, 42, ""],
                "bounty_labels": ["BACKEND", [], "python"],
            }
        }
    ) == {"python": ["python", "py", "backend"]}


def test_valid_structured_entries_keep_existing_deduplication_semantics():
    assert _normalise_tags(
        {
            "ci-cd": {
                "aliases": ["CI", "ci"],
                "bounty_labels": ["GitHub Actions", "CI-CD"],
            }
        }
    ) == {"ci-cd": ["ci-cd", "ci", "github actions"]}
