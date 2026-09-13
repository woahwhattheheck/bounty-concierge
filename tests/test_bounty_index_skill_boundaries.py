# SPDX-License-Identifier: MIT
"""Boundary regressions for bounty-index skill tagging."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import bounty_index


def test_rust_does_not_match_inside_trustworthy():
    assert "rust" not in bounty_index.tag_skills(
        "Write trustworthy deployment notes", ""
    )


def test_node_does_not_match_inside_inode():
    assert "javascript" not in bounty_index.tag_skills(
        "Inspect inode allocation", "filesystem metadata only"
    )


def test_post_does_not_match_inside_compost():
    assert "social-media" not in bounty_index.tag_skills(
        "Document compost processing", ""
    )


def test_filename_suffix_keywords_still_match():
    assert "python" in bounty_index.tag_skills("Fix worker.py parser", "")
    assert "rust" in bounty_index.tag_skills("Fix parser.rs", "")
    assert "javascript" in bounty_index.tag_skills("Fix worker.ts", "")


def test_word_and_punctuation_keywords_still_match():
    tags = bounty_index.tag_skills(
        "Rust + node.js CI/CD pipeline", "Publish a technical note on dev.to"
    )
    assert {"rust", "javascript", "ci/cd", "social-media"}.issubset(tags)


def test_multiword_keyword_still_matches_case_insensitively():
    assert "ci/cd" in bounty_index.tag_skills(
        "Repair GitHub Actions workflow", ""
    )
