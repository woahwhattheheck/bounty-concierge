# SPDX-License-Identifier: MIT
"""Allocate an executable portfolio from already-qualified ranked paid work.

This module is deliberately downstream of :mod:`concierge.opportunity_ranker`.
It does not establish bounty legitimacy, reward authority, availability,
earnings, settlement, or payment.  It consumes only rows that the canonical
ranker already accepted, plus explicit operator capacity/deadline/collision
constraints, then finds the exact maximum-estimated-EV feasible subset.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Union

from concierge.opportunity_ranker import (
    OpportunityRankInputError,
    _exact_decimal as _ranker_exact_decimal,
    _format_decimal as _ranker_format_decimal,
    rank_opportunities,
)


class PortfolioInputError(ValueError):
    """Raised when exact portfolio allocation cannot be performed reliably."""


_MAX_EXACT_CANDIDATES = 20
_MAX_RANKER_METRIC_TEXT_CHARS = 256


def _exact_decimal(value: Any, name: str) -> Decimal:
    """Apply the canonical bounded operator-input decimal contract."""
    try:
        return _ranker_exact_decimal(value, name)
    except OpportunityRankInputError as exc:
        raise PortfolioInputError(str(exc)) from exc


def _ranker_metric_decimal(value: Any, name: str) -> Decimal:
    """Parse a bounded fixed-point metric emitted by the canonical ranker.

    Ranker inputs are representation-bounded before arithmetic, but fixed-point
    output can legitimately contain more coefficient digits after exponent
    expansion (for example ``1e64`` renders as 65 integer digits).  The
    allocator therefore accepts only bounded, strip-stable, exponent-free
    ranker receipt strings rather than re-applying the stricter input parser.
    """
    if type(value) is not str or not value or value != value.strip():
        raise PortfolioInputError(f"{name} must be a canonical fixed-point string")
    if len(value) > _MAX_RANKER_METRIC_TEXT_CHARS or "e" in value.lower():
        raise PortfolioInputError(f"{name} exceeds the bounded ranker receipt format")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PortfolioInputError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise PortfolioInputError(f"{name} must be a finite decimal")
    return parsed


def _format_decimal(value: Decimal, *, metric: bool = False) -> str:
    """Render through the canonical boundary-safe ranker formatter."""
    return _ranker_format_decimal(value, metric=metric)


def _portfolio_excluded(
    item: dict[str, Any], code: str, *, detail: Optional[str] = None
) -> dict[str, Any]:
    result = {
        "input_index": item["input_index"],
        "canonical_source_url": item["canonical_source_url"],
        "reason_code": code,
    }
    if detail is not None:
        result["detail"] = detail
    return result


def _collision_group(candidate: dict[str, Any], source: str) -> str:
    value = candidate.get("collision_group")
    if value is None:
        return f"source:{source}"
    if type(value) is not str or not value.strip():
        raise PortfolioInputError("collision_group must be a non-empty string or null")
    return value.strip()


def _row_to_item(
    row: dict[str, Any], candidates: list[dict[str, Any]], capacity: Decimal
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    index = row.get("input_index")
    source = row.get("canonical_source_url")
    if isinstance(index, bool) or not isinstance(index, int):
        raise PortfolioInputError("ranker row input_index must be an integer")
    if index < 0 or index >= len(candidates):
        raise PortfolioInputError("ranker row input_index is out of range")
    if type(source) is not str or not source:
        raise PortfolioInputError("ranker row canonical source is invalid")
    candidate = candidates[index]
    if type(candidate) is not dict:
        raise PortfolioInputError("ranked candidate must be an object")

    try:
        deadline = _exact_decimal(
            candidate.get("hours_until_deadline"),
            f"candidates[{index}].hours_until_deadline",
        )
        group = _collision_group(candidate, source)
        effort = _ranker_metric_decimal(
            row.get("estimated_effort_hours"), "ranker.estimated_effort_hours"
        )
        reward = _ranker_metric_decimal(
            row.get("advertised_reward_usd"), "ranker.advertised_reward_usd"
        )
        probability = _ranker_metric_decimal(
            row.get("estimated_win_probability"),
            "ranker.estimated_win_probability",
        )
        skill = _ranker_metric_decimal(row.get("skill_match"), "ranker.skill_match")
    except PortfolioInputError as exc:
        shell = {"input_index": index, "canonical_source_url": source}
        return None, _portfolio_excluded(
            shell, "PORTFOLIO_CONSTRAINT_INVALID", detail=str(exc)
        )

    if deadline <= 0:
        shell = {"input_index": index, "canonical_source_url": source}
        return None, _portfolio_excluded(
            shell,
            "PORTFOLIO_CONSTRAINT_INVALID",
            detail="hours_until_deadline must be greater than zero",
        )
    if effort <= 0 or reward <= 0 or probability < 0 or probability > 1:
        raise PortfolioInputError("ranker emitted an invalid accepted metric")
    if skill < 0 or skill > 1:
        raise PortfolioInputError("ranker emitted an invalid skill score")

    expected_value = reward * probability
    item = {
        "input_index": index,
        "canonical_source_url": source,
        "rank": row.get("rank"),
        "advertised_reward_usd": reward,
        "estimated_win_probability": probability,
        "estimated_effort_hours": effort,
        "estimated_expected_value_usd": expected_value,
        "estimated_ev_per_hour_usd": expected_value / effort,
        "skill_match": skill,
        "hours_until_deadline": deadline,
        "collision_group": group,
    }
    if effort > capacity:
        return None, _portfolio_excluded(item, "EXCEEDS_PORTFOLIO_CAPACITY")
    if effort > deadline:
        return None, _portfolio_excluded(item, "DEADLINE_INFEASIBLE_ALONE")
    return item, None


def _better(
    candidate: dict[str, Any], incumbent: Optional[dict[str, Any]]
) -> bool:
    if incumbent is None:
        return True
    if candidate["expected_value"] != incumbent["expected_value"]:
        return candidate["expected_value"] > incumbent["expected_value"]
    # Equal estimated value must never consume more scarce operator capacity
    # merely to improve a secondary evidence score (for example, zero-EV work).
    if candidate["hours"] != incumbent["hours"]:
        return candidate["hours"] < incumbent["hours"]
    if candidate["skill_sum"] != incumbent["skill_sum"]:
        return candidate["skill_sum"] > incumbent["skill_sum"]
    return candidate["sources"] < incumbent["sources"]


def _exact_select(items: list[dict[str, Any]], capacity: Decimal) -> list[int]:
    """Solve the bounded portfolio exactly in earliest-deadline order.

    All work is assumed available at allocation time and executed serially.
    For any chosen subset, earliest-deadline-first scheduling is feasible iff
    cumulative effort at each selected deadline does not exceed that deadline.
    DFS therefore tests that exact condition while also enforcing capacity and
    one selection per explicit collision group.
    """
    ordered = sorted(
        range(len(items)),
        key=lambda i: (
            items[i]["hours_until_deadline"],
            items[i]["canonical_source_url"],
            items[i]["input_index"],
        ),
    )
    suffix_ev = [Decimal(0)] * (len(ordered) + 1)
    for pos in range(len(ordered) - 1, -1, -1):
        suffix_ev[pos] = (
            suffix_ev[pos + 1]
            + items[ordered[pos]]["estimated_expected_value_usd"]
        )

    best: Optional[dict[str, Any]] = None
    chosen: list[int] = []
    used_groups: set[str] = set()

    def visit(
        pos: int,
        used_hours: Decimal,
        expected_value: Decimal,
        skill_sum: Decimal,
    ) -> None:
        nonlocal best
        if best is not None and expected_value + suffix_ev[pos] < best["expected_value"]:
            return
        if pos == len(ordered):
            sources = tuple(
                sorted(items[index]["canonical_source_url"] for index in chosen)
            )
            solution = {
                "indices": tuple(chosen),
                "hours": used_hours,
                "expected_value": expected_value,
                "skill_sum": skill_sum,
                "sources": sources,
            }
            if _better(solution, best):
                best = solution
            return

        item_index = ordered[pos]
        item = items[item_index]

        # Excluding the item is always feasible.
        visit(pos + 1, used_hours, expected_value, skill_sum)

        group = item["collision_group"]
        if group in used_groups:
            return
        next_hours = used_hours + item["estimated_effort_hours"]
        if next_hours > capacity or next_hours > item["hours_until_deadline"]:
            return

        used_groups.add(group)
        chosen.append(item_index)
        visit(
            pos + 1,
            next_hours,
            expected_value + item["estimated_expected_value_usd"],
            skill_sum + item["skill_match"],
        )
        chosen.pop()
        used_groups.remove(group)

    visit(0, Decimal(0), Decimal(0), Decimal(0))
    if best is None:
        return []
    return list(best["indices"])


def allocate_portfolio(
    candidates: list[dict[str, Any]],
    skills: list[str],
    capacity_hours: Union[str, int, Decimal],
    *,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Return an exact maximum-estimated-EV feasible portfolio.

    ``hours_until_deadline`` and optional ``collision_group`` are operator
    constraints on each candidate.  A missing collision group defaults to a
    unique group derived from canonical source, so unrelated work does not
    conflict.  The exact solver is intentionally bounded to 20 feasible ranked
    candidates; larger inputs fail closed instead of silently switching to a
    heuristic.
    """
    if type(candidates) is not list:
        raise PortfolioInputError("candidates must be a list")
    capacity = _exact_decimal(capacity_hours, "capacity_hours")
    if capacity <= 0:
        raise PortfolioInputError("capacity_hours must be greater than zero")

    try:
        ranking = rank_opportunities(
            candidates,
            skills,
            saturation_threshold=saturation_threshold,
        )
    except OpportunityRankInputError as exc:
        raise PortfolioInputError(str(exc)) from exc

    ranked = ranking.get("ranked")
    ranking_excluded = ranking.get("excluded")
    if type(ranked) is not list or type(ranking_excluded) is not list:
        raise PortfolioInputError("canonical ranker returned an invalid result")

    feasible: list[dict[str, Any]] = []
    portfolio_excluded: list[dict[str, Any]] = []
    for row in ranked:
        if type(row) is not dict:
            raise PortfolioInputError("canonical ranker returned a malformed row")
        item, excluded = _row_to_item(row, candidates, capacity)
        if excluded is not None:
            portfolio_excluded.append(excluded)
        elif item is not None:
            feasible.append(item)

    if len(feasible) > _MAX_EXACT_CANDIDATES:
        raise PortfolioInputError(
            f"exact allocator supports at most {_MAX_EXACT_CANDIDATES} feasible ranked candidates"
        )

    selected_indices = set(_exact_select(feasible, capacity))
    selected_items = [feasible[index] for index in selected_indices]
    selected_items.sort(
        key=lambda item: (
            item["hours_until_deadline"],
            item["canonical_source_url"],
            item["input_index"],
        )
    )

    selected_sources = {item["canonical_source_url"] for item in selected_items}
    for item in feasible:
        if item["canonical_source_url"] not in selected_sources:
            portfolio_excluded.append(
                _portfolio_excluded(item, "NOT_IN_MAX_ESTIMATED_EV_PORTFOLIO")
            )

    selected: list[dict[str, Any]] = []
    cumulative = Decimal(0)
    total_ev = Decimal(0)
    for order, item in enumerate(selected_items, start=1):
        cumulative += item["estimated_effort_hours"]
        total_ev += item["estimated_expected_value_usd"]
        selected.append(
            {
                "allocation_order": order,
                "input_index": item["input_index"],
                "rank": item["rank"],
                "canonical_source_url": item["canonical_source_url"],
                "collision_group": item["collision_group"],
                "hours_until_deadline": _format_decimal(
                    item["hours_until_deadline"]
                ),
                "cumulative_allocated_hours": _format_decimal(cumulative),
                "advertised_reward_usd": _format_decimal(
                    item["advertised_reward_usd"]
                ),
                "estimated_win_probability": _format_decimal(
                    item["estimated_win_probability"]
                ),
                "estimated_effort_hours": _format_decimal(
                    item["estimated_effort_hours"]
                ),
                "estimated_expected_value_usd": _format_decimal(
                    item["estimated_expected_value_usd"], metric=True
                ),
                "estimated_ev_per_hour_usd": _format_decimal(
                    item["estimated_ev_per_hour_usd"], metric=True
                ),
                "skill_match": _format_decimal(item["skill_match"], metric=True),
                "authority": {
                    "eligibility": "canonical_opportunity_ranker",
                    "reward": "advertised_only",
                    "probability_effort_deadline": "operator_inputs",
                    "selection": "exact_bounded_optimizer",
                    "revenue": "not_earned_or_settled_by_this_receipt",
                },
            }
        )

    portfolio_excluded.sort(key=lambda row: row["input_index"])
    remaining = capacity - cumulative
    return {
        "schema": "qualified-opportunity-portfolio/v1",
        "candidate_count": len(candidates),
        "ranked_count": len(ranked),
        "selected_count": len(selected),
        "capacity_hours": _format_decimal(capacity),
        "selected_effort_hours": _format_decimal(cumulative),
        "remaining_capacity_hours": _format_decimal(remaining),
        "estimated_portfolio_expected_value_usd": _format_decimal(
            total_ev, metric=True
        ),
        "selected": selected,
        "ranking_excluded": ranking_excluded,
        "portfolio_excluded": portfolio_excluded,
        "optimizer": {
            "mode": "exact",
            "max_feasible_candidates": _MAX_EXACT_CANDIDATES,
            "objective": "maximize_estimated_expected_value_usd",
            "deadline_model": "serial_earliest_deadline_first",
            "collision_model": "at_most_one_per_collision_group",
            "tie_breaks": [
                "lower_total_effort_hours",
                "higher_total_skill_match",
                "lexicographically_smaller_canonical_source_set",
            ],
        },
        "authority": {
            "eligibility": "canonical_opportunity_ranker",
            "reward": "advertised_only",
            "operator_estimates": True,
            "cash_claim": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    return (
        f"selected={result['selected_count']} ranked={result['ranked_count']} "
        f"effort_hours={result['selected_effort_hours']}/{result['capacity_hours']} "
        f"estimated_portfolio_ev_usd={result['estimated_portfolio_expected_value_usd']} "
        "cash_claim=false"
    )


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if type(payload) is not dict:
        raise PortfolioInputError("request JSON must contain an object")
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.portfolio_allocator",
        description=(
            "Select an exact bounded portfolio from canonically qualified paid work."
        ),
    )
    parser.add_argument("request", help="JSON request path, or - for stdin")
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="Forwarded to canonical opportunity ranking (default: 4)",
    )
    parser.add_argument("--summary", action="store_true", help="Emit one-line summary")
    args = parser.parse_args(argv)

    try:
        payload = _load_request(args.request)
        result = allocate_portfolio(
            payload.get("candidates"),
            payload.get("skills", []),
            payload.get("capacity_hours"),
            saturation_threshold=args.saturation_threshold,
        )
    except (OSError, json.JSONDecodeError, PortfolioInputError) as exc:
        parser.error(str(exc))

    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["selected_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
