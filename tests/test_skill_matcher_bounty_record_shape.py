# SPDX-License-Identifier: MIT
"""Focused shape-resilience tests for incoming bounty records."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import skill_matcher


def test_null_scalar_fields_and_labels_contribute_no_search_text():
    bounty = {
        "title": None,
        "body": None,
        "labels": None,
        "difficulty": None,
    }

    assert skill_matcher._bounty_text(bounty).strip() == ""
    assert skill_matcher.match_skills(bounty, ["python"]) == 0.0


def test_mixed_label_list_ignores_non_string_entries():
    bounty = {
        "title": "",
        "body": "",
        "labels": [None, "Python", 42, {"name": "testing"}, "Backend"],
        "difficulty": "",
    }

    assert "python backend" in skill_matcher._bounty_text(bounty)
    assert skill_matcher.match_skills(bounty, ["python"]) == 1.0


def test_tuple_labels_preserve_string_label_matching():
    bounty = {"labels": ("Testing", "Backend")}

    assert skill_matcher.match_skills(bounty, ["testing"]) == 1.0


def test_malformed_label_container_contributes_no_search_text():
    bounty = {
        "title": "Documentation cleanup",
        "labels": {"python": True},
    }

    assert skill_matcher.match_skills(bounty, ["python"]) == 0.0
    assert skill_matcher.match_skills(bounty, ["documentation"]) == 1.0


def test_recommend_keeps_healthy_ranking_when_one_record_is_malformed():
    bounties = [
        {
            "id": "malformed",
            "title": None,
            "body": None,
            "labels": None,
            "difficulty": None,
        },
        {
            "id": "healthy",
            "title": "Add Python pytest coverage",
            "labels": ["Testing"],
        },
    ]

    result = skill_matcher.recommend(bounties, ["python", "testing"], limit=2)

    assert [item["id"] for item in result] == ["healthy", "malformed"]
    assert result[0]["match_score"] == 1.0
    assert result[1]["match_score"] == 0.0
