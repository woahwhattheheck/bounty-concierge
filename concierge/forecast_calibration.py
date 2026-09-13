# SPDX-License-Identifier: MIT
"""Deterministic calibration for paid-work probability and effort forecasts.

This module is deliberately downstream of opportunity qualification and upstream
of ranking/allocation.  It calibrates estimates only.  It grants no authority
about eligibility, ownership, reward, submission, award, settlement, payment,
or revenue.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Iterable


CALIBRATION_SCHEMA = "opportunity-forecast-calibration/v1"
RECEIPT_SCHEMA = "opportunity-forecast-calibration-receipt/v1"
HISTORY_SCHEMA = "opportunity-forecast-history/v1"
CANDIDATE_SCHEMA = "opportunity-forecast-candidates/v1"

_MAX_TEXT = 256
_MAX_RECORDS = 10000
_MAX_CANDIDATES = 1000
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class ForecastCalibrationError(ValueError):
    """Raised when calibration evidence is structurally or temporally unsafe."""


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ForecastCalibrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_strict_json(text: str) -> Any:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 4_000_000:
        raise ForecastCalibrationError("JSON input is missing or too large")
    try:
        return json.loads(text, object_pairs_hook=_pairs_object, parse_constant=lambda x: (_ for _ in ()).throw(ForecastCalibrationError(f"non-finite JSON constant: {x}")))
    except ForecastCalibrationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ForecastCalibrationError("invalid JSON") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > _MAX_TEXT:
        raise ForecastCalibrationError(f"{name} must be a bounded non-empty canonical string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ForecastCalibrationError(f"{name} has invalid format")
    return value


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
        raise ForecastCalibrationError(f"{name} must be an exact decimal string, integer, or Decimal")
    if isinstance(value, str):
        if not value or value != value.strip() or len(value) > 128:
            raise ForecastCalibrationError(f"{name} has invalid decimal representation")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ForecastCalibrationError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 64 or abs(parsed.as_tuple().exponent) > 64:
        raise ForecastCalibrationError(f"{name} exceeds exact decimal bounds")
    return parsed


def _format_decimal(value: Decimal, places: int = 6) -> str:
    if not value.is_finite():
        raise ForecastCalibrationError("cannot format non-finite decimal")
    with localcontext() as ctx:
        ctx.prec = max(64, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + places + 8)
        quantum = Decimal(1).scaleb(-places)
        out = value.quantize(quantum)
    text = format(out, "f").rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    if not _UTC.fullmatch(text):
        raise ForecastCalibrationError(f"{name} must be canonical millisecond UTC")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ForecastCalibrationError(f"{name} is invalid") from exc
    rendered = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    if rendered != text:
        raise ForecastCalibrationError(f"{name} must be canonical millisecond UTC")
    return parsed


def _probability(value: Any, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0 or result > 1:
        raise ForecastCalibrationError(f"{name} must be between 0 and 1")
    return result


def _positive_hours(value: Any, name: str) -> Decimal:
    result = _decimal(value, name)
    if result <= 0 or result > Decimal("1000000"):
        raise ForecastCalibrationError(f"{name} must be > 0 and <= 1000000")
    return result


def _confidence(resolved: int, effort_rows: int, minimum_samples: int) -> str:
    usable = min(resolved, effort_rows)
    if usable < minimum_samples:
        return "INSUFFICIENT"
    if usable >= 30:
        return "HIGH"
    if usable >= 10:
        return "MODERATE"
    return "LOW"


def _normalize_history(document: Any, cutoff: datetime, lookback_days: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if type(document) is not dict or document.get("schema") != HISTORY_SCHEMA or type(document.get("records")) is not list:
        raise ForecastCalibrationError(f"history must be {HISTORY_SCHEMA}")
    records = document["records"]
    if len(records) > _MAX_RECORDS:
        raise ForecastCalibrationError("history exceeds record limit")

    by_record: dict[str, dict[str, Any]] = {}
    candidate_to_record: dict[str, str] = {}
    facts = {"input_rows": len(records), "exact_duplicate_rows_collapsed": 0, "unresolved_rows": 0, "outside_lookback_rows": 0, "resolved_rows_used": 0}
    lower = cutoff - timedelta(days=lookback_days)

    for index, raw in enumerate(records):
        if type(raw) is not dict:
            raise ForecastCalibrationError(f"history.records[{index}] must be an object")
        allowed = {"record_id", "candidate_id", "segment", "forecasted_win_probability", "estimated_effort_hours", "outcome", "actual_effort_hours", "closed_at", "source_sha256"}
        if set(raw) != allowed:
            raise ForecastCalibrationError(f"history.records[{index}] has unexpected or missing keys")
        record_id = _text(raw["record_id"], f"history.records[{index}].record_id", pattern=_ID)
        candidate_id = _text(raw["candidate_id"], f"history.records[{index}].candidate_id", pattern=_ID)
        segment = _text(raw["segment"], f"history.records[{index}].segment", pattern=_ID)
        forecast = _probability(raw["forecasted_win_probability"], f"history.records[{index}].forecasted_win_probability")
        estimated = _positive_hours(raw["estimated_effort_hours"], f"history.records[{index}].estimated_effort_hours")
        source_sha = _text(raw["source_sha256"], f"history.records[{index}].source_sha256", pattern=_HEX64)
        outcome = raw["outcome"]
        if outcome not in {"won", "lost", "unresolved"}:
            raise ForecastCalibrationError(f"history.records[{index}].outcome is invalid")

        if outcome == "unresolved":
            if raw["closed_at"] is not None or raw["actual_effort_hours"] is not None:
                raise ForecastCalibrationError("unresolved history cannot carry closed_at or actual_effort_hours")
            normalized = {
                "record_id": record_id, "candidate_id": candidate_id, "segment": segment,
                "forecasted_win_probability": _format_decimal(forecast), "estimated_effort_hours": _format_decimal(estimated),
                "outcome": outcome, "actual_effort_hours": None, "closed_at": None, "source_sha256": source_sha,
            }
        else:
            closed = _timestamp(raw["closed_at"], f"history.records[{index}].closed_at")
            if closed > cutoff:
                raise ForecastCalibrationError("resolved history closes after trusted cutoff")
            actual = _positive_hours(raw["actual_effort_hours"], f"history.records[{index}].actual_effort_hours")
            normalized = {
                "record_id": record_id, "candidate_id": candidate_id, "segment": segment,
                "forecasted_win_probability": _format_decimal(forecast), "estimated_effort_hours": _format_decimal(estimated),
                "outcome": outcome, "actual_effort_hours": _format_decimal(actual),
                "closed_at": closed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "source_sha256": source_sha,
            }

        prior = by_record.get(record_id)
        if prior is not None:
            if prior != normalized:
                raise ForecastCalibrationError(f"changed replay for history record_id {record_id}")
            facts["exact_duplicate_rows_collapsed"] += 1
            continue
        by_record[record_id] = normalized

        other_record = candidate_to_record.get(candidate_id)
        if other_record is not None:
            raise ForecastCalibrationError(f"multiple history records for candidate_id {candidate_id}: {other_record}, {record_id}")
        candidate_to_record[candidate_id] = record_id

    used: list[dict[str, Any]] = []
    for record in sorted(by_record.values(), key=lambda row: row["record_id"]):
        if record["outcome"] == "unresolved":
            facts["unresolved_rows"] += 1
            continue
        closed = _timestamp(record["closed_at"], "normalized.closed_at")
        if closed < lower:
            facts["outside_lookback_rows"] += 1
            continue
        facts["resolved_rows_used"] += 1
        used.append(record)
    return used, facts


def _normalize_candidates(document: Any) -> list[dict[str, Any]]:
    if type(document) is not dict or document.get("schema") != CANDIDATE_SCHEMA or type(document.get("candidates")) is not list:
        raise ForecastCalibrationError(f"candidates must be {CANDIDATE_SCHEMA}")
    rows = document["candidates"]
    if len(rows) > _MAX_CANDIDATES:
        raise ForecastCalibrationError("candidate set exceeds limit")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if type(raw) is not dict:
            raise ForecastCalibrationError(f"candidates[{index}] must be an object")
        allowed = {"candidate_id", "canonical_source_url", "segment", "estimated_win_probability", "estimated_effort_hours", "source_sha256"}
        if set(raw) != allowed:
            raise ForecastCalibrationError(f"candidates[{index}] has unexpected or missing keys")
        candidate_id = _text(raw["candidate_id"], f"candidates[{index}].candidate_id", pattern=_ID)
        if candidate_id in seen:
            raise ForecastCalibrationError(f"duplicate candidate_id: {candidate_id}")
        seen.add(candidate_id)
        source_url = _text(raw["canonical_source_url"], f"candidates[{index}].canonical_source_url")
        if not (source_url.startswith("https://") or source_url.startswith("http://localhost/")):
            raise ForecastCalibrationError("canonical_source_url must be https:// (or localhost for fixtures)")
        segment = _text(raw["segment"], f"candidates[{index}].segment", pattern=_ID)
        probability = _probability(raw["estimated_win_probability"], f"candidates[{index}].estimated_win_probability")
        effort = _positive_hours(raw["estimated_effort_hours"], f"candidates[{index}].estimated_effort_hours")
        source_sha = _text(raw["source_sha256"], f"candidates[{index}].source_sha256", pattern=_HEX64)
        normalized.append({
            "candidate_id": candidate_id, "canonical_source_url": source_url, "segment": segment,
            "estimated_win_probability": _format_decimal(probability), "estimated_effort_hours": _format_decimal(effort),
            "source_sha256": source_sha,
        })
    normalized.sort(key=lambda row: row["candidate_id"])
    return normalized


def _segment_stats(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for record in records:
        segment = record["segment"]
        bucket = stats.setdefault(segment, {"resolved": 0, "wins": 0, "brier_sum": Decimal(0), "effort_rows": 0, "estimated_effort_sum": Decimal(0), "actual_effort_sum": Decimal(0)})
        forecast = Decimal(record["forecasted_win_probability"])
        actual = Decimal(1 if record["outcome"] == "won" else 0)
        bucket["resolved"] += 1
        bucket["wins"] += int(record["outcome"] == "won")
        bucket["brier_sum"] += (forecast - actual) * (forecast - actual)
        bucket["effort_rows"] += 1
        bucket["estimated_effort_sum"] += Decimal(record["estimated_effort_hours"])
        bucket["actual_effort_sum"] += Decimal(record["actual_effort_hours"])
    return stats


def _calibrate_probability(original: Decimal, stats: dict[str, Any] | None, minimum_samples: int, prior_strength: Decimal) -> tuple[Decimal, list[str], Decimal | None]:
    reasons: list[str] = []
    if not stats or stats["resolved"] < minimum_samples:
        reasons.append("INSUFFICIENT_RESOLVED_HISTORY")
        return original, reasons, None
    n = Decimal(stats["resolved"])
    empirical = (Decimal(stats["wins"]) + Decimal(1)) / (n + Decimal(2))
    weight = n / (n + prior_strength)
    calibrated = original * (Decimal(1) - weight) + empirical * weight
    return calibrated, reasons, empirical


def _calibrate_effort(original: Decimal, stats: dict[str, Any] | None, minimum_samples: int, prior_strength: Decimal) -> tuple[Decimal, Decimal, list[str], Decimal | None]:
    reasons: list[str] = []
    if not stats or stats["effort_rows"] < minimum_samples:
        reasons.append("INSUFFICIENT_EFFORT_HISTORY")
        return original, Decimal(1), reasons, None
    n = Decimal(stats["effort_rows"])
    empirical_ratio = stats["actual_effort_sum"] / stats["estimated_effort_sum"]
    factor = (prior_strength + n * empirical_ratio) / (prior_strength + n)
    if factor < Decimal("0.25"):
        factor = Decimal("0.25")
        reasons.append("EFFORT_FACTOR_FLOORED")
    elif factor > Decimal("4"):
        factor = Decimal("4")
        reasons.append("EFFORT_FACTOR_CAPPED")
    return original * factor, factor, reasons, empirical_ratio


def compile_forecast_calibration(
    history_document: Any,
    candidate_document: Any,
    trusted_cutoff: str,
    *,
    lookback_days: int = 180,
    minimum_samples: int = 3,
    probability_prior_strength: str | int | Decimal = "4",
    effort_prior_strength: str | int | Decimal = "4",
) -> dict[str, Any]:
    cutoff = _timestamp(trusted_cutoff, "trusted_cutoff")
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or not (1 <= lookback_days <= 3650):
        raise ForecastCalibrationError("lookback_days must be an integer in [1, 3650]")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or not (1 <= minimum_samples <= 1000):
        raise ForecastCalibrationError("minimum_samples must be an integer in [1, 1000]")
    p_prior = _positive_hours(probability_prior_strength, "probability_prior_strength")
    e_prior = _positive_hours(effort_prior_strength, "effort_prior_strength")

    history, history_facts = _normalize_history(history_document, cutoff, lookback_days)
    candidates = _normalize_candidates(candidate_document)
    stats = _segment_stats(history)

    calibrated: list[dict[str, Any]] = []
    for candidate in candidates:
        segment_stats = stats.get(candidate["segment"])
        original_p = Decimal(candidate["estimated_win_probability"])
        original_e = Decimal(candidate["estimated_effort_hours"])
        calibrated_p, p_reasons, empirical_rate = _calibrate_probability(original_p, segment_stats, minimum_samples, p_prior)
        calibrated_e, effort_factor, e_reasons, empirical_ratio = _calibrate_effort(original_e, segment_stats, minimum_samples, e_prior)
        resolved = int(segment_stats["resolved"]) if segment_stats else 0
        effort_rows = int(segment_stats["effort_rows"]) if segment_stats else 0
        brier = segment_stats["brier_sum"] / Decimal(resolved) if segment_stats and resolved else None
        calibrated.append({
            "candidate_id": candidate["candidate_id"],
            "canonical_source_url": candidate["canonical_source_url"],
            "segment": candidate["segment"],
            "source_sha256": candidate["source_sha256"],
            "original_estimated_win_probability": candidate["estimated_win_probability"],
            "calibrated_estimated_win_probability": _format_decimal(calibrated_p),
            "original_estimated_effort_hours": candidate["estimated_effort_hours"],
            "calibrated_estimated_effort_hours": _format_decimal(calibrated_e),
            "effort_calibration_factor": _format_decimal(effort_factor),
            "resolved_sample_count": resolved,
            "effort_sample_count": effort_rows,
            "confidence": _confidence(resolved, effort_rows, minimum_samples),
            "historical_brier_score": None if brier is None else _format_decimal(brier),
            "smoothed_empirical_win_rate": None if empirical_rate is None else _format_decimal(empirical_rate),
            "empirical_effort_ratio": None if empirical_ratio is None else _format_decimal(empirical_ratio),
            "reason_codes": sorted(set(p_reasons + e_reasons)),
            "authority": {
                "eligibility": "not_established",
                "reward": "not_established",
                "ownership": "not_established",
                "forecast": "calibrated_estimate_only",
                "submission": False,
                "award": False,
                "settlement": False,
                "payment": False,
                "revenue": False,
            },
        })

    result = {
        "schema": CALIBRATION_SCHEMA,
        "trusted_cutoff": trusted_cutoff,
        "parameters": {
            "lookback_days": lookback_days,
            "minimum_samples": minimum_samples,
            "probability_prior_strength": _format_decimal(p_prior),
            "effort_prior_strength": _format_decimal(e_prior),
        },
        "history_digest_sha256": _digest({"schema": HISTORY_SCHEMA, "records": history}),
        "candidate_set_digest_sha256": _digest({"schema": CANDIDATE_SCHEMA, "candidates": candidates}),
        "history_facts": history_facts,
        "segment_count": len(stats),
        "candidate_count": len(candidates),
        "calibrated": calibrated,
    }
    unsigned_receipt = {
        "schema": RECEIPT_SCHEMA,
        "calibration_sha256": _digest(result),
        "history_digest_sha256": result["history_digest_sha256"],
        "candidate_set_digest_sha256": result["candidate_set_digest_sha256"],
        "trusted_cutoff": trusted_cutoff,
        "parameters": result["parameters"],
    }
    result["receipt"] = {**unsigned_receipt, "receipt_sha256": _digest(unsigned_receipt)}
    return result


def verify_forecast_calibration(calibration: Any, history_document: Any, candidate_document: Any) -> bool:
    try:
        if type(calibration) is not dict or calibration.get("schema") != CALIBRATION_SCHEMA:
            return False
        receipt = calibration.get("receipt")
        if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA:
            return False
        expected_keys = {"schema", "calibration_sha256", "history_digest_sha256", "candidate_set_digest_sha256", "trusted_cutoff", "parameters", "receipt_sha256"}
        if set(receipt) != expected_keys:
            return False
        unsigned_receipt = {key: receipt[key] for key in expected_keys if key != "receipt_sha256"}
        if receipt["receipt_sha256"] != _digest(unsigned_receipt):
            return False
        rebuilt = compile_forecast_calibration(
            history_document,
            candidate_document,
            receipt["trusted_cutoff"],
            lookback_days=receipt["parameters"]["lookback_days"],
            minimum_samples=receipt["parameters"]["minimum_samples"],
            probability_prior_strength=receipt["parameters"]["probability_prior_strength"],
            effort_prior_strength=receipt["parameters"]["effort_prior_strength"],
        )
        return rebuilt == calibration
    except (ForecastCalibrationError, KeyError, TypeError, ValueError):
        return False


def apply_calibration_to_ranker_candidate(candidate: dict[str, Any], calibration_row: dict[str, Any], receipt_sha256: str) -> dict[str, Any]:
    """Return a copied ranker candidate with a matching calibration applied.

    The helper requires the target to carry the immutable calibration metadata
    fields.  It never mutates the input and never changes reward/snapshot data.
    """
    if type(candidate) is not dict or type(calibration_row) is not dict:
        raise ForecastCalibrationError("candidate and calibration_row must be objects")
    receipt_sha256 = _text(receipt_sha256, "receipt_sha256", pattern=_HEX64)
    bindings = {
        "candidate_id": candidate.get("calibration_candidate_id"),
        "canonical_source_url": candidate.get("calibration_canonical_source_url"),
        "segment": candidate.get("calibration_segment"),
        "source_sha256": candidate.get("calibration_source_sha256"),
    }
    for key, actual in bindings.items():
        if actual != calibration_row.get(key):
            raise ForecastCalibrationError(f"calibration transplant rejected: {key} mismatch")
    original_p = _format_decimal(_probability(candidate.get("estimated_win_probability"), "estimated_win_probability"))
    original_e = _format_decimal(_positive_hours(candidate.get("estimated_effort_hours"), "estimated_effort_hours"))
    if original_p != calibration_row.get("original_estimated_win_probability") or original_e != calibration_row.get("original_estimated_effort_hours"):
        raise ForecastCalibrationError("calibration changed-replay rejected: original estimates mismatch")
    result = copy.deepcopy(candidate)
    result["estimated_win_probability"] = calibration_row["calibrated_estimated_win_probability"]
    result["estimated_effort_hours"] = calibration_row["calibrated_estimated_effort_hours"]
    result["forecast_calibration"] = {
        "schema": RECEIPT_SCHEMA,
        "receipt_sha256": receipt_sha256,
        "confidence": calibration_row["confidence"],
        "resolved_sample_count": calibration_row["resolved_sample_count"],
        "effort_sample_count": calibration_row["effort_sample_count"],
        "authority": "estimate_only",
    }
    return result


def _read(path: str) -> Any:
    return load_strict_json(Path(path).read_text(encoding="utf-8"))


def _write_create_exclusive(path: str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False))
        handle.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile or verify deterministic paid-work forecast calibration receipts")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--history", required=True)
    compile_cmd.add_argument("--candidates", required=True)
    compile_cmd.add_argument("--trusted-cutoff", required=True)
    compile_cmd.add_argument("--output", required=True)
    compile_cmd.add_argument("--lookback-days", type=int, default=180)
    compile_cmd.add_argument("--minimum-samples", type=int, default=3)
    compile_cmd.add_argument("--probability-prior-strength", default="4")
    compile_cmd.add_argument("--effort-prior-strength", default="4")
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--history", required=True)
    verify_cmd.add_argument("--candidates", required=True)
    verify_cmd.add_argument("--receipt", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        history = _read(args.history)
        candidates = _read(args.candidates)
        if args.command == "compile":
            result = compile_forecast_calibration(
                history, candidates, args.trusted_cutoff,
                lookback_days=args.lookback_days,
                minimum_samples=args.minimum_samples,
                probability_prior_strength=args.probability_prior_strength,
                effort_prior_strength=args.effort_prior_strength,
            )
            _write_create_exclusive(args.output, result)
            print(result["receipt"]["receipt_sha256"])
            return 0
        receipt = _read(args.receipt)
        valid = verify_forecast_calibration(receipt, history, candidates)
        print("VALID" if valid else "INVALID")
        return 0 if valid else 2
    except (ForecastCalibrationError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
