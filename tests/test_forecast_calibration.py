from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from concierge.forecast_calibration import (  # noqa: E402
    CANDIDATE_SCHEMA,
    HISTORY_SCHEMA,
    ForecastCalibrationError,
    apply_calibration_to_ranker_candidate,
    compile_forecast_calibration,
    load_strict_json,
    verify_forecast_calibration,
)

CUT = "2026-09-13T12:00:00.000Z"


def sha(char: str) -> str:
    return char * 64


def history(count: int = 6, *, segment: str = "external-contract"):
    rows = []
    for i in range(count):
        won = i in {0, 1, 2, 3}
        rows.append({
            "record_id": f"hist-{i}",
            "candidate_id": f"old-{i}",
            "segment": segment,
            "forecasted_win_probability": "0.8" if i < 3 else "0.6",
            "estimated_effort_hours": "10",
            "outcome": "won" if won else "lost",
            "actual_effort_hours": "15" if i % 2 == 0 else "5",
            "closed_at": f"2026-09-{6+i:02d}T10:00:00.000Z",
            "source_sha256": sha("a"),
        })
    return {"schema": HISTORY_SCHEMA, "records": rows}


def candidates():
    return {"schema": CANDIDATE_SCHEMA, "candidates": [{
        "candidate_id": "new-1",
        "canonical_source_url": "https://example.test/work/1",
        "segment": "external-contract",
        "estimated_win_probability": "0.9",
        "estimated_effort_hours": "8",
        "source_sha256": sha("b"),
    }]}


def test_compile_is_deterministic_and_shrinks_probability_and_effort():
    one = compile_forecast_calibration(history(), candidates(), CUT)
    two = compile_forecast_calibration(history(), candidates(), CUT)
    assert one == two
    row = one["calibrated"][0]
    assert row["confidence"] == "LOW"
    assert row["calibrated_estimated_win_probability"] != "0.9"
    assert row["calibrated_estimated_effort_hours"] == "8"
    # historical effort totals happen to equal estimated totals (60h each)
    assert row["effort_calibration_factor"] == "1"
    assert row["resolved_sample_count"] == 6
    assert one["receipt"]["receipt_sha256"]
    assert verify_forecast_calibration(one, history(), candidates())


def test_insufficient_history_preserves_estimates_and_says_why():
    result = compile_forecast_calibration(history(2), candidates(), CUT, minimum_samples=3)
    row = result["calibrated"][0]
    assert row["confidence"] == "INSUFFICIENT"
    assert row["calibrated_estimated_win_probability"] == "0.9"
    assert row["calibrated_estimated_effort_hours"] == "8"
    assert set(row["reason_codes"]) == {"INSUFFICIENT_EFFORT_HISTORY", "INSUFFICIENT_RESOLVED_HISTORY"}


def test_unresolved_does_not_count_as_loss_or_effort_sample():
    doc = history(3)
    doc["records"].append({
        "record_id": "open-1", "candidate_id": "open-1", "segment": "external-contract",
        "forecasted_win_probability": "0.99", "estimated_effort_hours": "100",
        "outcome": "unresolved", "actual_effort_hours": None, "closed_at": None, "source_sha256": sha("c"),
    })
    result = compile_forecast_calibration(doc, candidates(), CUT)
    assert result["history_facts"]["unresolved_rows"] == 1
    assert result["calibrated"][0]["resolved_sample_count"] == 3


def test_unresolved_cannot_masquerade_with_terminal_fields():
    doc = history(3)
    doc["records"][0]["outcome"] = "unresolved"
    with pytest.raises(ForecastCalibrationError, match="unresolved history"):
        compile_forecast_calibration(doc, candidates(), CUT)


def test_future_terminal_outcome_rejected():
    doc = history(3)
    doc["records"][0]["closed_at"] = "2026-09-14T10:00:00.000Z"
    with pytest.raises(ForecastCalibrationError, match="trusted cutoff"):
        compile_forecast_calibration(doc, candidates(), CUT)


def test_exact_duplicate_record_collapses_but_changed_replay_fails():
    doc = history(3)
    doc["records"].append(copy.deepcopy(doc["records"][0]))
    result = compile_forecast_calibration(doc, candidates(), CUT)
    assert result["history_facts"]["exact_duplicate_rows_collapsed"] == 1
    changed = history(3)
    bad = copy.deepcopy(changed["records"][0])
    bad["outcome"] = "lost"
    changed["records"].append(bad)
    with pytest.raises(ForecastCalibrationError, match="changed replay"):
        compile_forecast_calibration(changed, candidates(), CUT)


def test_distinct_record_ids_cannot_double_count_same_historical_candidate():
    doc = history(3)
    dup = copy.deepcopy(doc["records"][0])
    dup["record_id"] = "hist-other"
    doc["records"].append(dup)
    with pytest.raises(ForecastCalibrationError, match="multiple history records"):
        compile_forecast_calibration(doc, candidates(), CUT)


def test_lookback_excludes_old_rows_without_turning_them_into_losses():
    doc = history(3)
    doc["records"][0]["closed_at"] = "2025-01-01T10:00:00.000Z"
    result = compile_forecast_calibration(doc, candidates(), CUT, lookback_days=30)
    assert result["history_facts"]["outside_lookback_rows"] == 1
    assert result["calibrated"][0]["resolved_sample_count"] == 2


def test_effort_outlier_factor_is_bounded():
    doc = history(3)
    for row in doc["records"]:
        row["actual_effort_hours"] = "1000000"
    result = compile_forecast_calibration(doc, candidates(), CUT, effort_prior_strength="0.1")
    row = result["calibrated"][0]
    assert row["effort_calibration_factor"] == "4"
    assert "EFFORT_FACTOR_CAPPED" in row["reason_codes"]


def test_receipt_detects_tamper_history_and_candidate_changes():
    result = compile_forecast_calibration(history(), candidates(), CUT)
    tampered = copy.deepcopy(result)
    tampered["calibrated"][0]["calibrated_estimated_win_probability"] = "1"
    assert not verify_forecast_calibration(tampered, history(), candidates())
    changed_history = history()
    changed_history["records"][0]["actual_effort_hours"] = "16"
    assert not verify_forecast_calibration(result, changed_history, candidates())
    changed_candidate = candidates()
    changed_candidate["candidates"][0]["source_sha256"] = sha("f")
    assert not verify_forecast_calibration(result, history(), changed_candidate)


def test_segment_transplant_and_changed_original_estimate_are_rejected_without_mutation():
    result = compile_forecast_calibration(history(), candidates(), CUT)
    row = result["calibrated"][0]
    target = {
        "snapshot": {"title": "opaque ranker data"},
        "estimated_win_probability": "0.9", "estimated_effort_hours": "8",
        "calibration_candidate_id": "new-1",
        "calibration_canonical_source_url": "https://example.test/work/1",
        "calibration_segment": "external-contract",
        "calibration_source_sha256": sha("b"),
    }
    before = copy.deepcopy(target)
    applied = apply_calibration_to_ranker_candidate(target, row, result["receipt"]["receipt_sha256"])
    assert target == before
    assert applied["estimated_win_probability"] == row["calibrated_estimated_win_probability"]
    bad = copy.deepcopy(target)
    bad["calibration_segment"] = "bounty"
    with pytest.raises(ForecastCalibrationError, match="transplant"):
        apply_calibration_to_ranker_candidate(bad, row, result["receipt"]["receipt_sha256"])
    changed = copy.deepcopy(target)
    changed["estimated_effort_hours"] = "9"
    with pytest.raises(ForecastCalibrationError, match="changed-replay"):
        apply_calibration_to_ranker_candidate(changed, row, result["receipt"]["receipt_sha256"])


def test_strict_json_rejects_duplicate_keys_and_nonfinite_constant():
    with pytest.raises(ForecastCalibrationError, match="duplicate JSON key"):
        load_strict_json('{"schema":"x","schema":"y"}')
    with pytest.raises(ForecastCalibrationError):
        load_strict_json('{"x":NaN}')


def test_noncanonical_timestamp_invalid_probability_and_float_rejected():
    doc = history(3)
    doc["records"][0]["closed_at"] = "2026-09-06T10:00:00Z"
    with pytest.raises(ForecastCalibrationError, match="canonical millisecond UTC"):
        compile_forecast_calibration(doc, candidates(), CUT)
    cand = candidates()
    cand["candidates"][0]["estimated_win_probability"] = "1.1"
    with pytest.raises(ForecastCalibrationError, match="between 0 and 1"):
        compile_forecast_calibration(history(3), cand, CUT)
    cand = candidates()
    cand["candidates"][0]["estimated_effort_hours"] = 8.0
    with pytest.raises(ForecastCalibrationError, match="exact decimal"):
        compile_forecast_calibration(history(3), cand, CUT)


def test_authority_ceiling_is_explicitly_false():
    row = compile_forecast_calibration(history(), candidates(), CUT)["calibrated"][0]
    auth = row["authority"]
    assert auth["forecast"] == "calibrated_estimate_only"
    for key in ("submission", "award", "settlement", "payment", "revenue"):
        assert auth[key] is False


def test_cli_compile_is_create_exclusive_and_verify_round_trip(tmp_path: Path):
    h = tmp_path / "history.json"
    c = tmp_path / "candidates.json"
    out = tmp_path / "receipt.json"
    h.write_text(json.dumps(history()), encoding="utf-8")
    c.write_text(json.dumps(candidates()), encoding="utf-8")
    module = ROOT / "concierge" / "forecast_calibration.py"
    cmd = [sys.executable, str(module), "compile", "--history", str(h), "--candidates", str(c), "--trusted-cutoff", CUT, "--output", str(out)]
    first = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr + first.stdout
    second = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert second.returncode == 2
    verify = subprocess.run([sys.executable, str(module), "verify", "--history", str(h), "--candidates", str(c), "--receipt", str(out)], capture_output=True, text=True, check=False)
    assert verify.returncode == 0
    assert "VALID" in verify.stdout
