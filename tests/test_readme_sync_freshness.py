from datetime import datetime, timezone
import json

import concierge.readme_sync as rs

ROW = {
    "repo": "Scottcjn/rustchain-bounties",
    "number": 504,
    "title": "Prometheus Metrics Exporter + Grafana",
    "url": "https://github.com/Scottcjn/rustchain-bounties/issues/504",
    "reward_rtc": 40.0,
    "difficulty": "standard",
    "skills": ["ci/cd", "documentation"],
}


def write_index(tmp_path, updated_at):
    path = tmp_path / "bounty_index.json"
    path.write_text(json.dumps({
        "updated_at": updated_at,
        "total_count": 1,
        "bounties": [ROW],
    }))
    return path


def test_fresh_snapshot_renders_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INDEX_PATH", write_index(tmp_path, "2026-09-12T00:00:00+00:00"))
    section = rs.build_section(require_fresh=True, now=datetime(2026, 9, 13, tzinfo=timezone.utc))
    assert "Prometheus Metrics" in section
    assert "Cached bounty index is stale" not in section


def test_stale_snapshot_hides_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INDEX_PATH", write_index(tmp_path, "2026-09-11T00:00:00+00:00"))
    section = rs.build_section(require_fresh=True, now=datetime(2026, 9, 13, tzinfo=timezone.utc))
    assert "Cached bounty index is stale" in section
    assert "2026-09-11T00:00:00+00:00" in section
    assert "Prometheus Metrics" not in section


def test_future_snapshot_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INDEX_PATH", write_index(tmp_path, "2026-09-13T00:06:00+00:00"))
    try:
        rs.build_section(require_fresh=True, now=datetime(2026, 9, 13, tzinfo=timezone.utc))
    except ValueError as exc:
        assert "future" in str(exc)
    else:
        assert False


def test_malformed_naive_and_non_utc_timestamps_fail(tmp_path, monkeypatch):
    for value in ("not-a-time", "2026-09-13T00:00:00", "2026-09-13T05:00:00+05:00"):
        monkeypatch.setattr(rs, "INDEX_PATH", write_index(tmp_path, value))
        try:
            rs.build_section(require_fresh=True, now=datetime(2026, 9, 13, tzinfo=timezone.utc))
        except ValueError:
            pass
        else:
            assert False, value


def test_direct_default_remains_backwards_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INDEX_PATH", write_index(tmp_path, "2000-01-01T00:00:00+00:00"))
    section = rs.build_section()
    assert "Prometheus Metrics" in section
