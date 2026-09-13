# SPDX-License-Identifier: MIT
"""Rank canonically qualified paid work without inventing cross-currency FX.

This module is deliberately additive to :mod:`concierge.opportunity_ranker`.
The existing ranker remains USD-only.  This surface accepts the same canonical
intake authority and operator estimates, but partitions USD and native RTC
opportunities before ranking them.  It never emits a USD-vs-RTC winner or
exchange-rate estimate.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, DecimalException, InvalidOperation
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any

from concierge.opportunity_ranker import (
    OpportunityRankInputError,
    _estimate,
    _exact_decimal,
    _format_decimal,
    _fraction_to_exact_decimal,
    _fraction_to_metric_decimal,
)
from concierge.revenue_intake import qualify_revenue_intake
from concierge.skill_matcher import match_skills


_MAX_JSON_BYTES = 4 * 1024 * 1024
_CURRENCIES = ("USD", "RTC")


class CurrencyPartitionedRankInputError(OpportunityRankInputError):
    """Raised when a partitioned ranking request is structurally unreliable."""


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


def _reward_signal_values(signals: dict[str, Any], field: str) -> list[Any]:
    values = signals.get(field, [])
    if values is None:
        return []
    if type(values) is not list:
        raise CurrencyPartitionedRankInputError(
            f"qualification signal {field} must be a list"
        )
    return values


def _currency_reward(signals: dict[str, Any], currency: str) -> Decimal | None:
    suffix = currency.casefold()
    raw_values: list[Any] = []
    for field in (
        f"advertised_reward_{suffix}",
        f"live_label_reward_{suffix}",
    ):
        raw_values.extend(_reward_signal_values(signals, field))

    distinct: set[Decimal] = set()
    for index, value in enumerate(raw_values):
        amount = _exact_decimal(
            value,
            f"authorized_{suffix}_reward[{index}]",
        )
        distinct.add(amount)

    if len(distinct) > 1:
        raise CurrencyPartitionedRankInputError(
            f"{currency} reward authority is ambiguous"
        )
    if not distinct:
        return None

    reward = next(iter(distinct))
    if reward <= 0:
        raise CurrencyPartitionedRankInputError(
            f"{currency} advertised reward must be greater than zero"
        )
    return reward


def _resolve_reward(intake: dict[str, Any]) -> tuple[str, Decimal]:
    qualification = intake.get("qualification")
    if type(qualification) is not dict:
        raise CurrencyPartitionedRankInputError(
            "intake qualification result is missing"
        )
    signals = qualification.get("signals")
    if type(signals) is not dict:
        raise CurrencyPartitionedRankInputError(
            "intake qualification signals are missing"
        )

    resolved: list[tuple[str, Decimal]] = []
    for currency in _CURRENCIES:
        reward = _currency_reward(signals, currency)
        if reward is not None:
            resolved.append((currency, reward))

    if len(resolved) != 1:
        raise CurrencyPartitionedRankInputError(
            "ACTIONABLE intake must expose exactly one authorized reward currency"
        )
    return resolved[0]


def _validate_request_shape(
    candidates: Any,
    skills: Any,
    saturation_threshold: Any,
) -> None:
    if type(candidates) is not list:
        raise CurrencyPartitionedRankInputError("candidates must be a list")
    if type(skills) is not list or any(
        type(skill) is not str or not skill.strip() for skill in skills
    ):
        raise CurrencyPartitionedRankInputError(
            "skills must be a list of non-empty strings"
        )
    if type(saturation_threshold) is not int:
        raise CurrencyPartitionedRankInputError(
            "saturation_threshold must be an integer"
        )
    if saturation_threshold <= 0:
        raise CurrencyPartitionedRankInputError(
            "saturation_threshold must be positive"
        )


def _score_candidate(
    *,
    index: int,
    source: str,
    currency: str,
    reward: Decimal,
    candidate: dict[str, Any],
    snapshot: dict[str, Any],
    skills: list[str],
) -> dict[str, Any]:
    effort, probability = _estimate(candidate)

    try:
        skill_score = Decimal(str(match_skills(snapshot, skills)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CurrencyPartitionedRankInputError(
            "skill score is invalid"
        ) from exc
    if not skill_score.is_finite() or skill_score < 0 or skill_score > 1:
        raise CurrencyPartitionedRankInputError("skill score is invalid")

    try:
        reward_fraction = Fraction(reward)
        effort_fraction = Fraction(effort)
        probability_fraction = Fraction(probability)
        expected_value_fraction = reward_fraction * probability_fraction
        ev_per_hour_fraction = expected_value_fraction / effort_fraction
        expected_value = _fraction_to_exact_decimal(expected_value_fraction)

        return {
            "input_index": index,
            "canonical_source_url": source,
            "currency": currency,
            "_reward_fraction": reward_fraction,
            "_effort_fraction": effort_fraction,
            "_probability_fraction": probability_fraction,
            "_expected_value_fraction": expected_value_fraction,
            "_ev_per_hour_fraction": ev_per_hour_fraction,
            "_skill_fraction": Fraction(skill_score),
            "_reward_text": _format_decimal(reward),
            "_effort_text": _format_decimal(effort),
            "_probability_text": _format_decimal(probability),
            "_expected_value_text": _format_decimal(
                expected_value, metric=True
            ),
            "_ev_per_hour_text": _format_decimal(
                _fraction_to_metric_decimal(ev_per_hour_fraction),
                metric=True,
            ),
            "_skill_text": _format_decimal(skill_score, metric=True),
        }
    except (DecimalException, OpportunityRankInputError, ZeroDivisionError) as exc:
        raise CurrencyPartitionedRankInputError(
            "estimate or reward cannot be scored exactly"
        ) from exc


def rank_partitioned_opportunities(
    candidates: list[dict[str, Any]],
    skills: list[str],
    *,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Rank ACTIONABLE opportunities inside USD and RTC partitions only.

    The canonical intake gate remains the authority for eligibility and reward
    evidence.  Operator-supplied effort/probability estimates are decision
    support only.  No conversion or ordering is performed between currencies.
    """

    _validate_request_shape(candidates, skills, saturation_threshold)

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        if type(candidate) is not dict:
            excluded.append(
                _excluded(index, source=None, code="CANDIDATE_INVALID")
            )
            continue
        snapshot = candidate.get("snapshot")
        if type(snapshot) is not dict:
            excluded.append(
                _excluded(index, source=None, code="SNAPSHOT_INVALID")
            )
            continue

        try:
            intake = qualify_revenue_intake(
                snapshot,
                saturation_threshold=saturation_threshold,
            )
        except (ValueError, TypeError, KeyError):
            excluded.append(
                _excluded(index, source=None, code="INTAKE_INVALID")
            )
            continue
        if type(intake) is not dict:
            excluded.append(
                _excluded(index, source=None, code="INTAKE_INVALID")
            )
            continue

        source = intake.get("canonical_source_url")
        if source is not None and type(source) is not str:
            excluded.append(
                _excluded(index, source=None, code="INTAKE_INVALID")
            )
            continue

        disposition = intake.get("disposition")
        dispatch = intake.get("dispatch")
        if disposition != "ACTIONABLE" or dispatch is not True:
            reasons = intake.get("reason_codes")
            if type(reasons) is not list or not all(
                type(reason) is str for reason in reasons
            ):
                reasons = []
            excluded.append(
                _excluded(
                    index,
                    source=source,
                    code="INTAKE_NOT_ACTIONABLE",
                    intake_disposition=(
                        disposition if type(disposition) is str else None
                    ),
                    intake_reason_codes=reasons,
                )
            )
            continue

        if type(source) is not str or not source:
            excluded.append(
                _excluded(
                    index,
                    source=None,
                    code="CANONICAL_SOURCE_MISSING",
                )
            )
            continue

        try:
            currency, reward = _resolve_reward(intake)
        except (CurrencyPartitionedRankInputError, OpportunityRankInputError):
            excluded.append(
                _excluded(
                    index,
                    source=source,
                    code="REWARD_CURRENCY_INVALID",
                )
            )
            continue

        try:
            effort, probability = _estimate(candidate)
        except OpportunityRankInputError:
            excluded.append(
                _excluded(index, source=source, code="ESTIMATE_INVALID")
            )
            continue

        try:
            # _score_candidate repeats _estimate only to keep all exact
            # arithmetic and skill validation in one isolated helper.
            scored = _score_candidate(
                index=index,
                source=source,
                currency=currency,
                reward=reward,
                candidate=candidate,
                snapshot=snapshot,
                skills=skills,
            )
        except CurrencyPartitionedRankInputError as exc:
            if "skill score" in str(exc):
                code = "SKILL_SCORE_INVALID"
            else:
                code = "ESTIMATE_INVALID"
            excluded.append(_excluded(index, source=source, code=code))
            continue

        # Ensure the values consumed by the helper are exactly the values
        # validated immediately above; this is defensive against future helper
        # drift, not an additional authority source.
        if (
            scored["_effort_fraction"] != Fraction(effort)
            or scored["_probability_fraction"] != Fraction(probability)
        ):
            excluded.append(
                _excluded(index, source=source, code="ESTIMATE_INVALID")
            )
            continue
        eligible.append(scored)

    # Duplicate canonical work is ambiguous globally, not once per currency:
    # the same source must never survive by being represented in two partitions.
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

    partition_rows: dict[str, list[dict[str, Any]]] = {
        currency: [] for currency in _CURRENCIES
    }
    for item in unique:
        partition_rows[item["currency"]].append(item)

    partitions: dict[str, dict[str, Any]] = {}
    total_ranked = 0
    for currency in _CURRENCIES:
        rows = partition_rows[currency]
        rows.sort(
            key=lambda item: (
                -item["_ev_per_hour_fraction"],
                -item["_expected_value_fraction"],
                -item["_skill_fraction"],
                -item["_reward_fraction"],
                item["_effort_fraction"],
                item["canonical_source_url"],
                item["input_index"],
            )
        )

        ranked: list[dict[str, Any]] = []
        for rank, item in enumerate(rows, start=1):
            ranked.append(
                {
                    "rank": rank,
                    "input_index": item["input_index"],
                    "canonical_source_url": item["canonical_source_url"],
                    "currency": currency,
                    "advertised_reward": item["_reward_text"],
                    "estimated_win_probability": item["_probability_text"],
                    "estimated_effort_hours": item["_effort_text"],
                    "estimated_expected_value": item["_expected_value_text"],
                    "estimated_ev_per_hour": item["_ev_per_hour_text"],
                    "skill_match": item["_skill_text"],
                    "authority": {
                        "eligibility": "canonical_intake_gate",
                        "reward": "advertised_only",
                        "probability_and_effort": "operator_estimates",
                        "revenue": "not_earned_or_settled_by_this_receipt",
                        "fx_conversion": False,
                    },
                }
            )

        total_ranked += len(ranked)
        partitions[currency] = {
            "currency": currency,
            "ranked_count": len(ranked),
            "ranked": ranked,
        }

    excluded.sort(key=lambda item: item["input_index"])

    return {
        "schema": "currency-partitioned-opportunity-ranking/v1",
        "ranking_basis_within_currency": [
            "estimated_ev_per_hour",
            "estimated_expected_value",
            "skill_match",
            "advertised_reward",
            "estimated_effort_hours",
            "canonical_source_url",
        ],
        "candidate_count": len(candidates),
        "ranked_count": total_ranked,
        "excluded_count": len(excluded),
        "partitions": partitions,
        "excluded": excluded,
        "authority": {
            "eligibility": "canonical_intake_gate",
            "reward": "advertised_only",
            "estimates": "operator_supplied",
            "cash_claim": False,
            "fx_conversion": False,
            "cross_currency_ranking": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    partitions = result.get("partitions")
    if type(partitions) is not dict:
        raise CurrencyPartitionedRankInputError(
            "ranking result partitions are missing"
        )
    usd = partitions.get("USD", {})
    rtc = partitions.get("RTC", {})
    return (
        f"ranked={result.get('ranked_count', 0)} "
        f"excluded={result.get('excluded_count', 0)} "
        f"usd_ranked={usd.get('ranked_count', 0)} "
        f"rtc_ranked={rtc.get('ranked_count', 0)} "
        "cross_currency_ranking=false fx_conversion=false cash_claim=false"
    )


def _strict_json_bytes(raw: bytes, *, source: str) -> dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        raise CurrencyPartitionedRankInputError(
            f"{source} exceeds {_MAX_JSON_BYTES} bytes"
        )

    def unique_object(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CurrencyPartitionedRankInputError(
                    f"{source} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        def reject_constant(value: str) -> Any:
            raise CurrencyPartitionedRankInputError(
                f"{source} contains non-standard numeric constant {value!r}"
            )

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except CurrencyPartitionedRankInputError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CurrencyPartitionedRankInputError(
            f"{source} is not valid bounded UTF-8 JSON"
        ) from exc
    if type(value) is not dict:
        raise CurrencyPartitionedRankInputError(
            "request JSON must contain an object"
        )
    return value


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
        return _strict_json_bytes(raw, source="stdin")
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise CurrencyPartitionedRankInputError(
                f"{path} exceeds {_MAX_JSON_BYTES} bytes"
            )
        raw = source.read_bytes()
    except CurrencyPartitionedRankInputError:
        raise
    except OSError as exc:
        raise CurrencyPartitionedRankInputError(
            f"cannot read {path}"
        ) from exc
    return _strict_json_bytes(raw, source=path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.currency_partitioned_ranker",
        description=(
            "Rank canonically qualified USD and native RTC work inside "
            "separate currency partitions; never perform FX."
        ),
    )
    parser.add_argument(
        "request",
        help="request JSON path, or '-' for stdin",
    )
    args = parser.parse_args(argv)

    try:
        request = _load_request(args.request)
        result = rank_partitioned_opportunities(
            request.get("candidates"),
            request.get("skills"),
            saturation_threshold=request.get("saturation_threshold", 4),
        )
    except (CurrencyPartitionedRankInputError, OpportunityRankInputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
