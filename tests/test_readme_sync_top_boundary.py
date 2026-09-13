# SPDX-License-Identifier: MIT
"""README top-N limits must not expand through negative slicing."""

import json

import pytest

from concierge import readme_sync as rs


def _bounties():
    return [
        {"repo": "o/r", "number": 1, "title": "one", "reward_rtc": 3},
        {"repo": "o/r", "number": 2, "title": "two", "reward_rtc": 2},
        {"repo": "o/r", "number": 3, "title": "three", "reward_rtc": 1},
    ]


def test_render_table_rejects_negative_top_n_and_keeps_zero_valid():
    with pytest.raises(ValueError, match="non-negative"):
        rs.render_table(_bounties(), top_n=-1)

    zero = rs.render_table(_bounties(), top_n=0)
    assert len(zero.splitlines()) == 2


def test_build_section_rejects_negative_top_n_before_reading_index(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(rs, "INDEX_PATH", missing)

    with pytest.raises(ValueError, match="non-negative"):
        rs.build_section(top_n=-1)


def test_cli_negative_top_returns_usage_error_without_mutating_files(tmp_path, monkeypatch, capsys):
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"updated_at": "now", "bounties": _bounties()}))
    readme_path = tmp_path / "README.md"
    original = "sentinel content must stay untouched"
    readme_path.write_text(original)
    monkeypatch.setattr(rs, "INDEX_PATH", index_path)
    monkeypatch.setattr(rs, "README_PATH", readme_path)

    assert rs.main(["--top", "-1"]) == 2
    assert readme_path.read_text() == original
    assert "non-negative integer" in capsys.readouterr().err
