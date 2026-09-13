# SPDX-License-Identifier: MIT
"""README sync must keep bounty-index text inside its Markdown boundary."""

from concierge import readme_sync as rs


def test_render_table_fences_controls_cells_and_link_delimiters():
    table = rs.render_table([{
        "repo": "owner/repo|fake\nrow",
        "number": 7,
        "title": "legit\n\\| forged\x1b[2J café 🚀",
        "url": "https://example.test/a)b(c\nx",
        "reward_rtc": 5,
        "difficulty": "major|critical\nx",
        "skills": ["python|rust", "docs\nforged"],
    }])

    assert len(table.splitlines()) == 3
    row = table.splitlines()[-1]
    assert "repo\\|fake\\x0arow" in row
    assert "#7" in row
    assert "legit\\x0a\\\\\\| forged\\x1b[2J café 🚀" in row
    assert "major\\|critical\\x0ax" in row
    assert "python\\|rust, docs\\x0aforged" in row
    assert "(https://example.test/a%29b%28c%0Ax)" in row
    assert "\x1b" not in row


def test_render_table_preserves_literal_backslashes_and_safe_pipe_parity():
    title = r"C:\t\n escaped \| plain |"
    row = rs.render_table([{
        "repo": "owner/project",
        "number": 1,
        "title": title,
        "reward_rtc": 10,
        "difficulty": "standard",
        "skills": [],
    }]).splitlines()[-1]

    assert r"C:\t\n" in row
    assert r"escaped \\\| plain \|" in row


def test_build_section_fences_updated_at_controls(tmp_path, monkeypatch):
    index_path = tmp_path / "bounty_index.json"
    index_path.write_text(
        '{"updated_at":"now\\nFORGED","bounties":[]}'
    )
    monkeypatch.setattr(rs, "INDEX_PATH", index_path)

    section = rs.build_section()

    assert "now\\x0aFORGED" in section
    assert "now\nFORGED" not in section
