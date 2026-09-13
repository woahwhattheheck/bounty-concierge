# SPDX-License-Identifier: MIT
"""Announcement formatting must keep untrusted bounty text in its lane."""

from concierge import announcer


def test_controls_cannot_forge_short_or_medium_rows():
    bounty = {
        "title": "legit\nforged\x1b[2J café 🚀",
        "rtc": "5\r999",
        "url": "https://example.test/a\x07b",
        "difficulty": "standard",
    }

    result = announcer.format_announcement([bounty])

    for key in ("short", "medium"):
        assert "\x1b" not in result[key]
        assert "\r" not in result[key]
        assert "\x07" not in result[key]
    assert "legit\\x0aforged\\x1b[2J café 🚀" in result["short"]
    assert "5\\x0d999 RTC" in result["medium"]


def test_long_markdown_escapes_table_cells_and_link_delimiters():
    bounty = {
        "title": "A | B\nC",
        "rtc": "5|10",
        "url": "https://example.test/a)b(c",
        "difficulty": "major|critical",
    }

    result = announcer.format_announcement([bounty])
    long = result["long"]

    assert "| A \\| B\\\\x0aC | 5\\|10 | major\\|critical |" in long
    assert "[link](https://example.test/a\\)b\\(c)" in long
    assert len([line for line in long.splitlines() if line.startswith("| ")]) == 2


def test_control_expansion_still_respects_short_limit():
    result = announcer.format_announcement(
        [{
            "title": ("\n" * 100) + ("A" * 300),
            "rtc": 1,
            "url": "https://example.test/1",
        }]
    )

    assert len(result["short"]) == 280
    assert result["short"].endswith("...")
    assert "\n" not in result["short"]
