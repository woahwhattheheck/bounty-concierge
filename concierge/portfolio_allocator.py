# SPDX-License-Identifier: MIT
"""Allocate canonically ranked paid work under an explicit operator-capacity budget.

This module is downstream of :mod:`concierge.opportunity_ranker`. It optimizes
only operator-supplied estimates for work that the existing intake/ranking
pipeline already classified as actionable. It does not establish funding,
assignment, acceptance, earned revenue, or settlement.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any


class PortfolioAllocationError(ValueError):
    """Raised when a portfolio request or ranking receipt is unreliable."""


_METRIC_QUANTUM = Decimal("0.000001")
_MAX_EXACT_COMBINATIONS = 2_000_000
_MAX_DECIMAL_DIGITS = 64
_MAX_ABS_DECIMAL_EXPONENT = 64


def _exact_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise PortfolioAllocationError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise PortfolioAllocationError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if isinstance(value, str) and not value.strip():
        raise PortfolioAllocationError(f"{name} must be non-empty")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PortfolioAllocationError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise PortfolioAllocationError(f"{name} must be a finite decimal")
    decimal_tuple = parsed.as_tuple()
    if (
        len(decimal_tuple.digits) > _MAX_DECIMAL_DIGITS
        or abs(decimal_tuple.exponent) > _MAX_ABS_DECIMAL_EXPONENT
    ):
        raise PortfolioAllocationError(f"{name} exceeds bounded decimal precision")
    return parsed


def _format_decimal(value: Decimal, *, metric: bool = False) -> str:
    if metric:
        with localcontext() as context:
            context.prec = 96
            value = value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_UP)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _metric_matches(observed: Decimal, exact: Decimal) -> bool:
    try:
        with localcontext() as context:
            context.prec = 96
            expected = exact.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return False
    return observed == expected


def _validate_ranked_row(row: Any, expected_rank: int) -> dict[str, Any]:
    if type(row) is not dict:
        raise PortfolioAllocationError("ranked rows must be objects")

    rank = row.get("rank")
    input_index = row.get("input_index")
    source = row.get("canonical_source_url")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank != expected_rank:
        raise PortfolioAllocationError("ranked rows must have contiguous ranks")
    if (
        isinstance(input_index, bool)
        or not isinstance(input_index, int)
        or input_index < 0
    ):
        raise PortfolioAllocationError("ranked input_index must be a non-negative integer")
    if type(source) is not str or not source:
        raise PortfolioAllocationError("ranked canonical_source_url must be non-empty")

    reward = _exact_decimal(row.get("advertised_reward_usd"), "advertised_reward_usd")
    probability = _exact_decimal(
        row.get("estimated_win_probability"), "estimated_win_probability"
    )
    effort = _exact_decimal(row.get("estimated_effort_hours"), "estimated_effort_hours")
    observed_ev = _exact_decimal(
        row.get("estimated_expected_value_usd"), "estimated_expected_value_usd"
    )
    observed_ev_per_hour = _exact_decimal(
        row.get("estimated_ev_per_hour_usd"), "estimated_ev_per_hour_usd"
    )
    skill = _exact_decimal(row.get("skill_match"), "skill_match")

    if reward <= 0:
        raise PortfolioAllocationError("advertised_reward_usd must be positive")
    if probability < 0 or probability > 1:
        raise PortfolioAllocationError("estimated_win_probability must be between 0 and 1")
    if effort <= 0:
        raise PortfolioAllocationError("estimated_effort_hours must be positive")
    if skill < 0 or skill > 1:
        raise PortfolioAllocationError("skill_match must be between 0 and 1")

    with localcontext() as context:
        context.prec = 28
        exact_ev = reward * probability
        exact_ev_per_hour = exact_ev / effort

    if not _metric_matches(observed_ev, exact_ev):
        raise PortfolioAllocationError(
            "estimated_expected_value_usd does not match reward * probability"
        )
    if not _metric_matches(observed_ev_per_hour, exact_ev_per_hour):
        raise PortfolioAllocationError(
            "estimated_ev_per_hour_usd does not match expected value / effort"
        )

    authority = row.get("authority")
    if type(authority) is not dict:
        raise PortfolioAllocationError("ranked authority is missing")
    if authority.get("reward") != "advertised_only":
        raise PortfolioAllocationError("ranked reward authority is not advertised_only")
    if authority.get("probability_and_effort") != "operator_estimates":
        raise PortfolioAllocationError(
            "ranked probability/effort authority is not operator_estimates"
        )
    if authority.get("revenue") != "not_earned_or_settled_by_this_receipt":
        raise PortfolioAllocationError("ranked revenue authority is unsafe")

    return {
        "rank": rank,
        "input_index": input_index,
        "canonical_source_url": source,
        "advertised_reward_usd": reward,
        "estimated_win_probability": probability,
        "estimated_effort_hours": effort,
        "estimated_expected_value_usd": exact_ev,
        "estimated_ev_per_hour_usd": exact_ev_per_hour,
        "skill_match": skill,
    }


def _validate_ranking(ranking: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if type(ranking) is not dict:
        raise PortfolioAllocationError("ranking must be an object")
    if ranking.get("schema") != "qualified-opportunity-ranking/v1":
        raise PortfolioAllocationError("unsupported ranking schema")

    authority = ranking.get("authority")
    if type(authority) is not dict:
        raise PortfolioAllocationError("ranking authority is missing")
    if authority.get("reward") != "advertised_only":
        raise PortfolioAllocationError("ranking reward authority is not advertised_only")
    if authority.get("estimates") != "operator_supplied":
        raise PortfolioAllocationError("ranking estimate authority is not operator_supplied")
    if authority.get("cash_claim") is not False:
        raise PortfolioAllocationError("ranking receipt must explicitly disclaim cash")

    ranked_raw = ranking.get("ranked")
    excluded = ranking.get("excluded")
    if type(ranked_raw) is not list or type(excluded) is not list:
        raise PortfolioAllocationError("ranking ranked/excluded fields must be lists")
    if ranking.get("ranked_count") != len(ranked_raw):
        raise PortfolioAllocationError("ranking ranked_count does not match ranked rows")
    if ranking.get("excluded_count") != len(excluded):
        raise PortfolioAllocationError("ranking excluded_count does not match excluded rows")
    candidate_count = ranking.get("candidate_count")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 0
        or candidate_count != len(ranked_raw) + len(excluded)
    ):
        raise PortfolioAllocationError(
            "ranking candidate_count does not match ranked + excluded rows"
        )

    ranked = [
        _validate_ranked_row(row, expected_rank=index)
        for index, row in enumerate(ranked_raw, start=1)
    ]
    sources = [row["canonical_source_url"] for row in ranked]
    indices = [row["input_index"] for row in ranked]
    if len(sources) != len(set(sources)):
        raise PortfolioAllocationError("ranking contains duplicate canonical sources")
    if len(indices) != len(set(indices)):
        raise PortfolioAllocationError("ranking contains duplicate input indices")

    ranked_indices = set(indices)
    safe_excluded: list[dict[str, Any]] = []
    excluded_indices: set[int] = set()
    for row in excluded:
        if type(row) is not dict:
            raise PortfolioAllocationError("excluded ranking rows must be objects")
        input_index = row.get("input_index")
        reason_code = row.get("reason_code")
        source = row.get("canonical_source_url")
        if (
            isinstance(input_index, bool)
            or not isinstance(input_index, int)
            or input_index < 0
        ):
            raise PortfolioAllocationError(
                "excluded input_index must be a non-negative integer"
            )
        if input_index in excluded_indices or input_index in ranked_indices:
            raise PortfolioAllocationError("ranking reuses an input_index")
        excluded_indices.add(input_index)
        if type(reason_code) is not str or not reason_code:
            raise PortfolioAllocationError("excluded reason_code must be non-empty")
        if source is not None and (type(source) is not str or not source):
            raise PortfolioAllocationError(
                "excluded canonical_source_url must be null or non-empty"
            )
        safe_excluded.append(
            {
                "input_index": input_index,
                "canonical_source_url": source,
                "reason_code": reason_code,
            }
        )
    if ranked_indices | excluded_indices != set(range(candidate_count)):
        raise PortfolioAllocationError(
            "ranking input indices do not cover the candidate batch exactly"
        )
    return ranked, safe_excluded


def _combination_upper_bound(candidate_count: int, max_active: int) -> int:
    limit = min(candidate_count, max_active)
    return sum(math.comb(candidate_count, count) for count in range(limit + 1))


def _is_better(
    *,
    ev: Decimal,
    used_hours: Decimal,
    skill_total: Decimal,
    sources: tuple[str, ...],
    incumbent: tuple[Decimal, Decimal, Decimal, tuple[str, ...]],
) -> bool:
    incumbent_ev, incumbent_hours, incumbent_skill, incumbent_sources = incumbent
    if ev != incumbent_ev:
        return ev > incumbent_ev
    if used_hours != incumbent_hours:
        return used_hours < incumbent_hours
    if skill_total != incumbent_skill:
        return skill_total > incumbent_skill
    return sources < incumbent_sources


def allocate_ranked_portfolio(
    ranking: dict[str, Any],
    *,
    available_hours: str | int | Decimal,
    max_active: int,
    reserve_hours: str | int | Decimal = "0",
    minimum_skill_match: str | int | Decimal = "0",
) -> dict[str, Any]:
    """Select the exact highest-estimated-EV portfolio under operator capacity.

    The search is exact, not greedy. Inputs remain operator estimates, and all
    money values in the result remain advertised/estimated rather than earned.
    """
    ranked, intake_excluded = _validate_ranking(ranking)

    available = _exact_decimal(available_hours, "available_hours")
    reserve = _exact_decimal(reserve_hours, "reserve_hours")
    minimum_skill = _exact_decimal(minimum_skill_match, "minimum_skill_match")
    if available <= 0:
        raise PortfolioAllocationError("available_hours must be positive")
    if reserve < 0 or reserve > available:
        raise PortfolioAllocationError(
            "reserve_hours must be between zero and available_hours"
        )
    if (
        isinstance(max_active, bool)
        or not isinstance(max_active, int)
        or max_active <= 0
    ):
        raise PortfolioAllocationError("max_active must be a positive integer")
    if minimum_skill < 0 or minimum_skill > 1:
        raise PortfolioAllocationError(
            "minimum_skill_match must be between zero and one"
        )

    allocatable = available - reserve
    policy_excluded: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in ranked:
        if row["skill_match"] < minimum_skill:
            policy_excluded.append(
                {
                    "input_index": row["input_index"],
                    "canonical_source_url": row["canonical_source_url"],
                    "reason_code": "BELOW_MINIMUM_SKILL",
                }
            )
        else:
            candidates.append(row)

    upper_bound = _combination_upper_bound(len(candidates), max_active)
    if upper_bound > _MAX_EXACT_COMBINATIONS:
        raise PortfolioAllocationError(
            "exact allocation search exceeds resource bound; reduce candidates "
            "or max_active explicitly"
        )

    best_indices: tuple[int, ...] = ()
    best_objective = (Decimal(0), Decimal(0), Decimal(0), ())
    evaluated = 1  # empty portfolio
    max_size = min(max_active, len(candidates))
    for size in range(1, max_size + 1):
        for combination in itertools.combinations(range(len(candidates)), size):
            evaluated += 1
            rows = [candidates[index] for index in combination]
            used_hours = sum(
                (row["estimated_effort_hours"] for row in rows), Decimal(0)
            )
            if used_hours > allocatable:
                continue
            total_ev = sum(
                (row["estimated_expected_value_usd"] for row in rows), Decimal(0)
            )
            total_skill = sum((row["skill_match"] for row in rows), Decimal(0))
            sources = tuple(sorted(row["canonical_source_url"] for row in rows))
            if _is_better(
                ev=total_ev,
                used_hours=used_hours,
                skill_total=total_skill,
                sources=sources,
                incumbent=best_objective,
            ):
                best_indices = combination
                best_objective = (total_ev, used_hours, total_skill, sources)

    selected_positions = set(best_indices)
    selected_rows = [candidates[index] for index in best_indices]
    selected_rows.sort(key=lambda row: row["rank"])

    selected: list[dict[str, Any]] = []
    for order, row in enumerate(selected_rows, start=1):
        selected.append(
            {
                "portfolio_order": order,
                "rank": row["rank"],
                "input_index": row["input_index"],
                "canonical_source_url": row["canonical_source_url"],
                "advertised_reward_usd": _format_decimal(
                    row["advertised_reward_usd"]
                ),
                "estimated_win_probability": _format_decimal(
                    row["estimated_win_probability"]
                ),
                "estimated_effort_hours": _format_decimal(
                    row["estimated_effort_hours"]
                ),
                "estimated_expected_value_usd": _format_decimal(
                    row["estimated_expected_value_usd"], metric=True
                ),
                "estimated_ev_per_hour_usd": _format_decimal(
                    row["estimated_ev_per_hour_usd"], metric=True
                ),
                "skill_match": _format_decimal(row["skill_match"], metric=True),
            }
        )

    not_selected = list(policy_excluded)
    for index, row in enumerate(candidates):
        if index not in selected_positions:
            not_selected.append(
                {
                    "input_index": row["input_index"],
                    "canonical_source_url": row["canonical_source_url"],
                    "reason_code": "CAPACITY_NOT_SELECTED",
                }
            )
    not_selected.sort(key=lambda row: row["input_index"])

    used_hours = sum(
        (row["estimated_effort_hours"] for row in selected_rows), Decimal(0)
    )
    total_ev = sum(
        (row["estimated_expected_value_usd"] for row in selected_rows), Decimal(0)
    )
    total_reward = sum(
        (row["advertised_reward_usd"] for row in selected_rows), Decimal(0)
    )

    return {
        "schema": "qualified-opportunity-portfolio/v1",
        "capacity": {
            "available_hours": _format_decimal(available),
            "reserve_hours": _format_decimal(reserve),
            "allocatable_hours": _format_decimal(allocatable),
            "max_active": max_active,
            "minimum_skill_match": _format_decimal(minimum_skill),
        },
        "optimization": {
            "objective": "maximum_estimated_expected_value_usd",
            "method": "exact_subset_search",
            "combination_upper_bound": upper_bound,
            "combinations_evaluated": evaluated,
            "tie_breaks": [
                "lower_estimated_effort_hours",
                "higher_total_skill_match",
                "canonical_source_url",
            ],
        },
        "selected_count": len(selected),
        "selected": selected,
        "not_selected": not_selected,
        "intake_excluded": intake_excluded,
        "metrics": {
            "used_hours": _format_decimal(used_hours),
            "unused_allocatable_hours": _format_decimal(allocatable - used_hours),
            "advertised_reward_usd_total": _format_decimal(total_reward),
            "estimated_expected_value_usd_total": _format_decimal(
                total_ev, metric=True
            ),
        },
        "authority": {
            "eligibility": "canonical_intake_gate_via_ranker",
            "reward": "advertised_only",
            "optimization_inputs": "operator_estimates",
            "earned_revenue_claim": False,
            "settled_cash_claim": False,
        },
    }


def allocate_portfolio(
    candidates: list[dict[str, Any]],
    skills: list[str],
    *,
    available_hours: str | int | Decimal,
    max_active: int,
    reserve_hours: str | int | Decimal = "0",
    minimum_skill_match: str | int | Decimal = "0",
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Run the canonical ranker, then allocate its actionable candidates."""
    from concierge.opportunity_ranker import rank_opportunities

    ranking = rank_opportunities(
        candidates, skills, saturation_threshold=saturation_threshold
    )
    return allocate_ranked_portfolio(
        ranking,
        available_hours=available_hours,
        max_active=max_active,
        reserve_hours=reserve_hours,
        minimum_skill_match=minimum_skill_match,
    )


def format_summary(result: dict[str, Any]) -> str:
    return (
        f"selected={result['selected_count']} "
        f"used_hours={result['metrics']['used_hours']} "
        f"estimated_ev_usd={result['metrics']['estimated_expected_value_usd_total']} "
        "earned_revenue_claim=false settled_cash_claim=false"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        value = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    if type(value) is not dict:
        raise PortfolioAllocationError("request JSON must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.portfolio_allocator",
        description=(
            "Allocate canonically qualified paid work under an explicit "
            "operator-capacity budget."
        ),
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument("--summary", action="store_true", help="Emit one-line summary")
    args = parser.parse_args(argv)

    try:
        payload = _load_request(args.request)
        common = {
            "available_hours": payload.get("available_hours"),
            "max_active": payload.get("max_active"),
            "reserve_hours": payload.get("reserve_hours", "0"),
            "minimum_skill_match": payload.get("minimum_skill_match", "0"),
        }
        if "ranking" in payload:
            if "candidates" in payload or "skills" in payload:
                raise PortfolioAllocationError(
                    "provide either ranking or candidates/skills, not both"
                )
            result = allocate_ranked_portfolio(payload["ranking"], **common)
        else:
            result = allocate_portfolio(
                payload.get("candidates"),
                payload.get("skills", []),
                saturation_threshold=payload.get("saturation_threshold", 4),
                **common,
            )
    except (
        OSError,
        json.JSONDecodeError,
        PortfolioAllocationError,
        TypeError,
        ValueError,
    ) as exc:
        parser.error(str(exc))

    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["selected_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
