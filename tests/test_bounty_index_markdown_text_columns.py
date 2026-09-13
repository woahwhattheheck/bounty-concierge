# SPDX-License-Identifier: MIT
"""Regression tests for Markdown custody across every textual bounty column."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concierge import bounty_index


def test_format_markdown_confines_repo_tier_and_skills_to_one_row():
    rendered = bounty_index.format_markdown(
        [
            {
                "number": 7,
                "repo": "owner/repo|spoof\r\n| injected",
                "title": "Safe title",
                "reward_rtc": 10,
                "difficulty": "standard|critical\nrow",
                "skills": ["python|rust", "docs\r\nnext"],
            }
        ]
    )

    assert len(rendered.splitlines()) == 3
    row = rendered.splitlines()[2]
    assert "repo\\|spoof \\| injected" in row
    assert "standard\\|critical row" in row
    assert "python\\|rust, docs next" in row


def test_format_markdown_preserves_ordinary_text_columns():
    rendered = bounty_index.format_markdown(
        [
            {
                "number": 8,
                "repo": "owner/healthy-repo",
                "title": "Healthy title",
                "reward_rtc": 25,
                "difficulty": "standard",
                "skills": ["python", "testing"],
            }
        ]
    )

    assert rendered.splitlines()[2] == (
        "| 8 | healthy-repo | Healthy title | 25.0 | standard | python, testing |"
    )
