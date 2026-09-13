# SPDX-License-Identifier: MIT
"""Rank already-qualified paid work by explicit, bounded operator estimates.

This module is intentionally downstream of ``revenue_intake``. It does not
establish whether a bounty is legitimate, available, funded, earned, or paid.
The existing canonical intake gate remains authoritative for eligibility.
"""

from __future__ import annotations

import argparse
import json
from decimal import (
    Decimal,
    DecimalException,
    InvalidOperation,
    ROUND_HALF_UP,
    localcontext,
)
from pathlib import Path
from typing import Any

from concierge.revenue_intake import qualify_revenue_intake
from concierge.skill_matcher import match_skills


class OpportunityRankInputError(ValueError):
    """Raised when the ranking request itself is structurally unreliable."""


_METRIC_QUANTUM = Decimal("0.000001")
_MAX_DECIMAL_TEXT_CHARS = 128
_MAX_DECIMAL_DIGITS = 64
_MAX_DECIMAL_ABS_EXPONENT = 64


def _exact_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise OpportunityRankInputError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise OpportunityRankInputError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise OpportunityRankInputError(f"{name} must be non-empty")
        if len(value) > _MAX_DECIMAL_TEXT_CHARS:
            raise OpportunityRankInputError(
                f"{name} exceeds the supported exact-decimal representation"
            )
    elif isinstance(value, int) and value.bit_length() > 256:
        raise OpportunityRankInputError(
            f"{name} exceeds the supported exact-decimal representation"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise OpportunityRankInputError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise OpportunityRankInputError(f"{name} must be a finite decimal")
    parts = parsed.as_tuple()
    if (
        len(parts.digits) > _MAX_DECIMAL_DIGITS
        or abs(parts.exponent) > _MAX_DECIMAL_ABS_EXPONENT
    ):
        raise OpportunityRankInputError(
            f"{name} exceeds the supported exact-decimal representation"
        )
    return parsed


def _format_decimal(value: Decimal, *, metric: bool = False) -> str:
    if metric:
        # Quantizing a large-but-bounded exact value to six decimal places can
        # legitimately need more than the process-wide Decimal precision. Size
        # the local context from the fixed-point result instead of allowing a
        # candidate-local display conversion to raise InvalidOperation.
        integer_digits = max(value.adjusted() + 1, 1)
        with localcontext() as context:
            context.prec = max(28, integer_digits + 6)
            value = value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_UP)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _estimate(candidate: dict[str, Any]) -> tuple[Decimal, Decimal]:
    effort = _exact_decimal(
        candidate.get("estimated_effort_hours"), "estimated_effort_hours"
    )
    probability = _exact_decimal(
        candidate.get("estimated_win_probability"), "estimated_win_probability"
    )
    if effort <= 0:
        raise OpportunityRankInputError("estimated_effort_hours must be greater than zero")
    if probability < 0 or probability > 1:
        raise OpportunityRankInputError(
            "estimated_win_probability must be between 0 and 1"
        )
    return effort, probability


def _reward_from_intake(intake: dict[str, Any]) -> Decimal:
    qualification = intake.get("qualification")
    if not isinstance(qualification, dict):
        raise OpportunityRankInputError("intake qualification result is missing")
    signals = qualification.get("signals")
    if not isinstance(signals, dict):
        raise OpportunityRankInputError("intake qualification signals are missing")

    raw_values: list[Any] = []
    for field in ("advertised_reward_usd", "live_label_reward_usd"):
        values = signals.get(field, [])
        if values is None:
            continue
        if not isinstance(values, list):
            raise OpportunityRankInputError(
                f"qualification signal {field} must be a list"
            )
        raw_values.extend(values)

    distinct: set[Decimal] = set()
    for index, value in enumerate(raw_values):
        distinct.add(_exact_decimal(value, f"authorized_reward[{index}]"))

    if len(distinct) != 1:
        raise OpportunityRankInputError(
            "ACTIONABLE intake must expose exactly one authorized USD reward"
        )
    reward = next(iter(distinct))
    if reward <= 0:
        raise OpportunityRankInputError("advertised reward must be greater than zero")
    return reward


def _excluded(
    index: int,
    *,
    source: str | None,
    code: str,
    intake_disposition: str | None = None,
    intake_reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "input_index": index,
        "canonical_source_url": source,
        "reason_code": code,
    }
    if intake_disposition is not None:
        result["intake_disposition"] = intake_disposition
    if intake_reason_codes:
        result["intake_reason_codes"] = list(intake_reason_codes)
    return result


def rank_opportunities(
    candidates: list[dict[str, Any]],
    skills: list[str],
    *,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Rank canonical ACTIONABLE candidates by estimated expected value per hour.

    Every candidate supplies a normalized ``snapshot`` for ``revenue_intake``,
    positive ``estimated_effort_hours``, and ``estimated_win_probability`` in
    [0, 1]. Decimal estimates should be strings in JSON. HOLD/REJECT candidates,
    malformed estimates/reward authority, and duplicate canonical issues are
    excluded rather than scored.
    """
    if type(candidates) is not list:
        raise OpportunityRankInputError("candidates must be a list")
    if type(skills) is not list or any(
        type(skill) is not str or not skill.strip() for skill in skills
    ):
        raise OpportunityRankInputError("skills must be a list of non-empty strings")
    if isinstance(saturation_threshold, bool) or not isinstance(
        saturation_threshold, int
    ):
        raise OpportunityRankInputError("saturation_threshold must be an integer")
    if saturation_threshold <= 0:
        raise OpportunityRankInputError("saturation_threshold must be positive")

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        if type(candidate) is not dict:
            excluded.append(_excluded(index, source=None, code="CANDIDATE_INVALID"))
            continue
        snapshot = candidate.get("snapshot")
        if type(snapshot) is not dict:
            excluded.append(_excluded(index, source=None, code="SNAPSHOT_INVALID"))
            continue

        try:
            intake = qualify_revenue_intake(
                snapshot, saturation_threshold=saturation_threshold
            )
        except (ValueError, TypeError, KeyError):
            excluded.append(_excluded(index, source=None, code="INTAKE_INVALID"))
            continue

        source = intake.get("canonical_source_url")
        if source is not None and type(source) is not str:
            excluded.append(_excluded(index, source=None, code="INTAKE_INVALID"))
            continue
        disposition = intake.get("disposition")
        dispatch = intake.get("dispatch")
        if disposition != "ACTIONABLE" or dispatch is not True:
            reason_codes = intake.get("reason_codes")
            if not isinstance(reason_codes, list) or not all(
                isinstance(reason, str) for reason in reason_codes
            ):
                reason_codes = []
            excluded.append(
                _excluded(
                    index,
                    source=source,
                    code="INTAKE_NOT_ACTIONABLE",
                    intake_disposition=(
                        disposition if isinstance(disposition, str) else None
                    ),
                    intake_reason_codes=reason_codes,
                )
            )
            continue
        if type(source) is not str or not source:
            excluded.append(
                _excluded(index, source=None, code="CANONICAL_SOURCE_MISSING")
            )
            continue

        try:
            reward = _reward_from_intake(intake)
            effort, probability = _estimate(candidate)
        except OpportunityRankInputError:
            excluded.append(
                _excluded(index, source=source, code="ESTIMATE_OR_REWARD_INVALID")
            )
            continue

        try:
            skill_score = Decimal(str(match_skills(snapshot, skills)))
        except (InvalidOperation, TypeError, ValueError):
            excluded.append(_excluded(index, source=source, code="SKILL_SCORE_INVALID"))
            continue
        if not skill_score.is_finite() or skill_score < 0 or skill_score > 1:
            excluded.append(_excluded(index, source=source, code="SKILL_SCORE_INVALID"))
            continue

        try:
            with localcontext() as context:
                context.prec = 28
                expected_value = reward * probability
                ev_per_hour = expected_value / effort

            reward_text = _format_decimal(reward)
            probability_text = _format_decimal(probability)
            effort_text = _format_decimal(effort)
            expected_value_text = _format_decimal(expected_value, metric=True)
            ev_per_hour_text = _format_decimal(ev_per_hour, metric=True)
            skill_match_text = _format_decimal(skill_score, metric=True)
        except DecimalException:
            excluded.append(
                _excluded(index, source=source, code="ESTIMATE_OR_REWARD_INVALID")
            )
            continue

        eligible.append(
            {
                "input_index": index,
                "canonical_source_url": source,
                "_reward": reward,
                "_effort": effort,
                "_probability": probability,
                "_expected_value": expected_value,
                "_ev_per_hour": ev_per_hour,
                "_skill_match": skill_score,
                "_reward_text": reward_text,
                "_probability_text": probability_text,
                "_effort_text": effort_text,
                "_expected_value_text": expected_value_text,
                "_ev_per_hour_text": ev_per_hour_text,
                "_skill_match_text": skill_match_text,
            }
        )

    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in eligible:
        by_source.setdefault(item["canonical_source_url"], []).append(item)

    unique: list[dict[str, Any]] = []
    for source, items in by_source.items():
        if len(items) > 1:
            for item in items:
                excluded.append(
                    _excluded(
                        item["input_index"],
                        source=source,
                        code="DUPLICATE_CANONICAL_SOURCE",
                    )
                )
        else:
            unique.append(items[0])

    unique.sort(
        key=lambda item: (
            -item["_ev_per_hour"],
            -item["_expected_value"],
            -item["_skill_match"],
            -item["_reward"],
            item["_effort"],
            item["canonical_source_url"],
            item["input_index"],
        )
    )

    ranked: list[dict[str, Any]] = []
    for rank, item in enumerate(unique, start=1):
        ranked.append(
            {
                "rank": rank,
                "input_index": item["input_index"],
                "canonical_source_url": item["canonical_source_url"],
                "advertised_reward_usd": item["_reward_text"],
                "estimated_win_probability": item["_probability_text"],
                "estimated_effort_hours": item["_effort_text"],
                "estimated_expected_value_usd": item["_expected_value_text"],
                "estimated_ev_per_hour_usd": item["_ev_per_hour_text"],
                "skill_match": item["_skill_match_text"],
                "authority": {
                    "eligibility": "canonical_intake_gate",
                    "reward": "advertised_only",
                    "probability_and_effort": "operator_estimates",
                    "revenue": "not_earned_or_settled_by_this_receipt",
                },
            }
        )

    excluded.sort(key=lambda item: item["input_index"])
    return {
        "schema": "qualified-opportunity-ranking/v1",
        "ranking_basis": [
            "estimated_ev_per_hour_usd",
            "estimated_expected_value_usd",
            "skill_match",
            "advertised_reward_usd",
            "estimated_effort_hours",
            "canonical_source_url",
        ],
        "candidate_count": len(candidates),
        "ranked_count": len(ranked),
        "excluded_count": len(excluded),
        "ranked": ranked,
        "excluded": excluded,
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "estimates": "operator_supplied",
            "cash_claim": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    top = result.get("ranked", [])
    top_source = top[0]["canonical_source_url"] if top else "none"
    top_ev = top[0]["estimated_ev_per_hour_usd"] if top else "none"
    return (
        f"ranked={result['ranked_count']} excluded={result['excluded_count']} "
        f"top_source={top_source} top_estimated_ev_per_hour_usd={top_ev} "
        "cash_claim=false"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        value = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    if type(value) is not dict:
        raise OpportunityRankInputError("request JSON must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.opportunity_ranker",
        description=(
            "Rank only canonically qualified paid work by explicit estimated "
            "expected value per operator hour."
        ),
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="Forwarded to canonical revenue intake (default: 4)",
    )
    parser.add_argument("--summary", action="store_true", help="Emit one-line summary")
    args = parser.parse_args(argv)

    try:
        payload = _load_request(args.request)
        result = rank_opportunities(
            payload.get("candidates"),
            payload.get("skills", []),
            saturation_threshold=args.saturation_threshold,
        )
    except (OSError, json.JSONDecodeError, OpportunityRankInputError) as exc:
        parser.error(str(exc))

    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ranked_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
