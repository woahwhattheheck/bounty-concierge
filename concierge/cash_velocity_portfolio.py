# SPDX-License-Identifier: MIT
"""Cash-velocity constrained portfolio allocation for qualified bounty work.

The existing cash-calibrated allocator remains authoritative for eligibility and
same-repository terminal-cash probability damping.  This module adds exactly one
new decision-support constraint: repositories whose independently verified
cash-cycle receipt is in ``READY_FOR_OWNER_CASH_CYCLE_REVIEW`` may consume only
an explicit operator-chosen fraction of total engineering capacity.

No RTC amount is converted to USD and transfer timing is never treated as proof
of payment, debt, receivables, accounting revenue, sponsor identity, or payout
authority.  The velocity layer changes selection constraints only; it does not
rewrite advertised rewards or rank metrics.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from concierge import cash_calibrated_portfolio as _cash
from concierge import cash_cycle_review as _cycle
from concierge import portfolio_allocator as _portfolio
from concierge.opportunity_ranker import OpportunityRankInputError, rank_opportunities


class CashVelocityInputError(ValueError):
    """Malformed, stale, or authority-incompatible velocity allocation input."""


_SCHEMA = "cash-velocity-opportunity-portfolio/v1"
_POLICY_SCHEMA = 1
_MAX_RECEIPT_AGE_HOURS = 24 * 365
_MAX_EXACT_CANDIDATES = 20
_MAX_JSON_BYTES = 4 * 1024 * 1024
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLOW_STATE = "READY_FOR_OWNER_CASH_CYCLE_REVIEW"


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CashVelocityInputError("value is not canonical JSON") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise CashVelocityInputError("canonical JSON value is too large")
    return encoded


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _parse_utc(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not _UTC_RE.fullmatch(value):
        raise CashVelocityInputError(
            f"{field} must be canonical UTC with at most microsecond precision"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CashVelocityInputError(f"{field} must be canonical UTC") from exc
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_decimal_text(value: Any, *, field: str) -> Decimal:
    if type(value) is not str or not value or value != value.strip() or len(value) > 128:
        raise CashVelocityInputError(f"{field} must be a bounded decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CashVelocityInputError(f"{field} must be a finite decimal string") from exc
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or len(parsed.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
    ):
        raise CashVelocityInputError(f"{field} must be a bounded finite decimal")
    return parsed


def _velocity_policy(raw: Any) -> Dict[str, Any]:
    expected = {
        "schema_version",
        "max_receipt_age_hours",
        "slow_repo_max_capacity_fraction",
    }
    if type(raw) is not dict or set(raw) != expected:
        raise CashVelocityInputError(
            "velocity_policy must contain only schema_version, max_receipt_age_hours, "
            "and slow_repo_max_capacity_fraction"
        )
    if raw.get("schema_version") != _POLICY_SCHEMA:
        raise CashVelocityInputError("velocity_policy.schema_version must be 1")
    age = raw.get("max_receipt_age_hours")
    if (
        isinstance(age, bool)
        or not isinstance(age, int)
        or age <= 0
        or age > _MAX_RECEIPT_AGE_HOURS
    ):
        raise CashVelocityInputError(
            f"max_receipt_age_hours must be an integer in 1..{_MAX_RECEIPT_AGE_HOURS}"
        )
    fraction = _bounded_decimal_text(
        raw.get("slow_repo_max_capacity_fraction"),
        field="slow_repo_max_capacity_fraction",
    )
    if fraction < 0 or fraction > 1:
        raise CashVelocityInputError(
            "slow_repo_max_capacity_fraction must be between 0 and 1"
        )
    return {
        "schema_version": 1,
        "max_receipt_age_hours": age,
        "slow_repo_max_capacity_fraction": _portfolio._format_decimal(fraction),
    }


def _github_issue_repo(source: Any) -> Optional[Tuple[str, str]]:
    if type(source) is not str or not source or source != source.strip():
        return None
    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    pieces = parsed.path.split("/")
    if len(pieces) != 5 or pieces[0] != "" or pieces[3] != "issues":
        return None
    owner, name, issue = pieces[1], pieces[2], pieces[4]
    if (
        not _SEGMENT_RE.fullmatch(owner)
        or not _SEGMENT_RE.fullmatch(name)
        or owner in {".", ".."}
        or name in {".", ".."}
        or not issue.isascii()
        or not issue.isdigit()
        or issue.startswith("0")
        or int(issue) <= 0
    ):
        return None
    display = f"{owner}/{name}"
    return display, display.casefold()


def _validate_cycle_authority(receipt: Any) -> Dict[str, Any]:
    if type(receipt) is not dict:
        raise CashVelocityInputError("cash_cycle_receipt must be an object")
    if receipt.get("schema_version") != 1 or receipt.get("product") != "bounty-cash-cycle-review/v1":
        raise CashVelocityInputError("cash_cycle_receipt schema/product is incompatible")
    fingerprint = receipt.get("receipt_sha256")
    if type(fingerprint) is not str or not _SHA256_RE.fullmatch(fingerprint):
        raise CashVelocityInputError("cash_cycle_receipt omitted lowercase SHA-256")
    authority = receipt.get("authority")
    required = {
        "owner_review_only": True,
        "payer_or_sponsor_identity_inference": False,
        "sponsor_or_maintainer_contact": False,
        "opportunity_rank_mutation": False,
        "wallet_or_provider_mutation": False,
        "payout_or_transfer_initiation": False,
        "debt_or_receivable_assertion": False,
        "cash_recognition": False,
        "revenue_recognition": False,
    }
    if type(authority) is not dict or any(authority.get(k) is not v for k, v in required.items()):
        raise CashVelocityInputError("cash_cycle_receipt authority ceiling is incompatible")
    return receipt


def _slow_repositories(receipt: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = receipt.get("repositories")
    if type(rows) is not list:
        raise CashVelocityInputError("cash_cycle_receipt.repositories must be a list")
    result: Dict[str, Dict[str, Any]] = {}
    seen: Set[str] = set()
    for row in rows:
        if type(row) is not dict:
            raise CashVelocityInputError("cash_cycle repository row must be an object")
        repo = row.get("repo")
        state = row.get("state")
        if type(repo) is not str or "/" not in repo or repo != repo.strip():
            raise CashVelocityInputError("cash_cycle repository identity is invalid")
        key = repo.casefold()
        if key in seen:
            raise CashVelocityInputError("cash_cycle receipt repeats repository identity")
        seen.add(key)
        if state == _SLOW_STATE:
            samples = row.get("confirmed_sample_count")
            median = row.get("median_last_confirmed_transfer_lag_seconds")
            if isinstance(samples, bool) or not isinstance(samples, int) or samples <= 0:
                raise CashVelocityInputError("slow repository row lacks confirmed samples")
            if type(median) is not str or not median:
                raise CashVelocityInputError("slow repository row lacks median transfer lag")
            _bounded_decimal_text(median, field="median_last_confirmed_transfer_lag_seconds")
            result[key] = {
                "repo": repo,
                "state": state,
                "confirmed_sample_count": samples,
                "median_last_confirmed_transfer_lag_seconds": median,
                "review_lag_hours": row.get("review_lag_hours"),
            }
    return result


def _apply_cash_calibration(
    candidates: List[Dict[str, Any]], calibration_rows: Any
) -> List[Dict[str, Any]]:
    if type(candidates) is not list:
        raise CashVelocityInputError("candidates must be a list")
    if type(calibration_rows) is not list:
        raise CashVelocityInputError("cash calibration rows must be a list")
    adjusted = copy.deepcopy(candidates)
    seen: Set[int] = set()
    for row in calibration_rows:
        if type(row) is not dict:
            raise CashVelocityInputError("cash calibration row must be an object")
        index = row.get("input_index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(adjusted)
            or index in seen
            or type(adjusted[index]) is not dict
        ):
            raise CashVelocityInputError("cash calibration row input_index is invalid")
        seen.add(index)
        original = _bounded_decimal_text(
            str(adjusted[index].get("estimated_win_probability")),
            field=f"candidates[{index}].estimated_win_probability",
        )
        calibrated = _bounded_decimal_text(
            row.get("calibrated_estimated_win_probability"),
            field="calibrated_estimated_win_probability",
        )
        if original < 0 or original > 1 or calibrated < 0 or calibrated > 1:
            raise CashVelocityInputError("win probability must remain between 0 and 1")
        if calibrated > original:
            raise CashVelocityInputError("cash calibration may not increase win probability")
        adjusted[index]["estimated_win_probability"] = _portfolio._format_decimal(calibrated)
    return adjusted


def _better(candidate: Dict[str, Any], incumbent: Optional[Dict[str, Any]]) -> bool:
    if incumbent is None:
        return True
    if candidate["expected_value"] != incumbent["expected_value"]:
        return candidate["expected_value"] > incumbent["expected_value"]
    if candidate["hours"] != incumbent["hours"]:
        return candidate["hours"] < incumbent["hours"]
    if candidate["skill_sum"] != incumbent["skill_sum"]:
        return candidate["skill_sum"] > incumbent["skill_sum"]
    return candidate["sources"] < incumbent["sources"]


def _exact_select_velocity(
    items: List[Dict[str, Any]],
    capacity: Decimal,
    repo_limits: Dict[str, Fraction],
) -> List[int]:
    ordered = sorted(
        range(len(items)),
        key=lambda i: (
            items[i]["_deadline_fraction"],
            items[i]["canonical_source_url"],
            items[i]["input_index"],
        ),
    )
    suffix_ev = [Fraction(0)] * (len(ordered) + 1)
    for pos in range(len(ordered) - 1, -1, -1):
        suffix_ev[pos] = suffix_ev[pos + 1] + items[ordered[pos]]["_expected_value_fraction"]

    capacity_fraction = Fraction(capacity)
    used_groups: Set[str] = set()
    repo_hours: Dict[str, Fraction] = {}
    chosen: List[int] = []
    best: Optional[Dict[str, Any]] = None

    def visit(pos: int, used_hours: Fraction, expected_value: Fraction, skill_sum: Fraction) -> None:
        nonlocal best
        if best is not None and expected_value + suffix_ev[pos] < best["expected_value"]:
            return
        if pos == len(ordered):
            sources = tuple(sorted(items[index]["canonical_source_url"] for index in chosen))
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
        visit(pos + 1, used_hours, expected_value, skill_sum)

        group = item["collision_group"]
        if group in used_groups:
            return
        next_hours = used_hours + item["_effort_fraction"]
        if next_hours > capacity_fraction or next_hours > item["_deadline_fraction"]:
            return
        repo_key = item.get("_velocity_repo_key")
        if repo_key in repo_limits:
            next_repo_hours = repo_hours.get(repo_key, Fraction(0)) + item["_effort_fraction"]
            if next_repo_hours > repo_limits[repo_key]:
                return
        else:
            next_repo_hours = None

        used_groups.add(group)
        chosen.append(item_index)
        prior_repo_hours = repo_hours.get(repo_key) if repo_key is not None else None
        if repo_key in repo_limits and next_repo_hours is not None:
            repo_hours[repo_key] = next_repo_hours
        visit(
            pos + 1,
            next_hours,
            expected_value + item["_expected_value_fraction"],
            skill_sum + item["_skill_fraction"],
        )
        if repo_key in repo_limits:
            if prior_repo_hours is None:
                repo_hours.pop(repo_key, None)
            else:
                repo_hours[repo_key] = prior_repo_hours
        chosen.pop()
        used_groups.remove(group)

    visit(0, Fraction(0), Fraction(0), Fraction(0))
    return [] if best is None else list(best["indices"])


def _select_velocity_portfolio(
    adjusted_candidates: List[Dict[str, Any]],
    skills: List[str],
    capacity_hours: Any,
    slow_repos: Dict[str, Dict[str, Any]],
    slow_fraction: Decimal,
    *,
    saturation_threshold: int,
) -> Dict[str, Any]:
    capacity = _portfolio._exact_decimal(capacity_hours, "capacity_hours")
    if capacity <= 0:
        raise CashVelocityInputError("capacity_hours must be greater than zero")
    try:
        ranking = rank_opportunities(
            adjusted_candidates,
            skills,
            saturation_threshold=saturation_threshold,
        )
    except OpportunityRankInputError as exc:
        raise CashVelocityInputError(str(exc)) from exc
    ranked = ranking.get("ranked")
    ranking_excluded = ranking.get("excluded")
    if type(ranked) is not list or type(ranking_excluded) is not list:
        raise CashVelocityInputError("canonical ranker returned an invalid result")

    cap_fraction = Fraction(slow_fraction)
    total_fraction = Fraction(capacity)
    repo_limits = {key: total_fraction * cap_fraction for key in slow_repos}
    feasible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for row in ranked:
        if type(row) is not dict:
            raise CashVelocityInputError("canonical ranker returned malformed row")
        try:
            item, rejected = _portfolio._row_to_item(row, adjusted_candidates, capacity)
        except _portfolio.PortfolioInputError as exc:
            raise CashVelocityInputError(str(exc)) from exc
        if rejected is not None:
            excluded.append(rejected)
            continue
        if item is None:
            continue
        repo = _github_issue_repo(item["canonical_source_url"])
        repo_key = repo[1] if repo else None
        item["_velocity_repo_key"] = repo_key
        item["_velocity_repo_display"] = repo[0] if repo else None
        if repo_key in repo_limits and item["_effort_fraction"] > repo_limits[repo_key]:
            excluded.append(
                _portfolio._portfolio_excluded(
                    item,
                    "EXCEEDS_REPO_VELOCITY_CAP",
                    detail=(
                        f"repo={repo[0]} cap_hours="
                        f"{_portfolio._format_decimal(_portfolio._fraction_to_exact_decimal(repo_limits[repo_key]))}"
                    ),
                )
            )
            continue
        feasible.append(item)

    if len(feasible) > _MAX_EXACT_CANDIDATES:
        raise CashVelocityInputError(
            f"exact velocity allocator supports at most {_MAX_EXACT_CANDIDATES} feasible ranked candidates"
        )

    selected_indices = set(_exact_select_velocity(feasible, capacity, repo_limits))
    selected_items = [feasible[index] for index in selected_indices]
    selected_items.sort(
        key=lambda item: (
            item["_deadline_fraction"],
            item["canonical_source_url"],
            item["input_index"],
        )
    )
    selected_sources = {item["canonical_source_url"] for item in selected_items}
    for item in feasible:
        if item["canonical_source_url"] not in selected_sources:
            excluded.append(
                _portfolio._portfolio_excluded(item, "NOT_IN_MAX_CASH_VELOCITY_PORTFOLIO")
            )

    selected: List[Dict[str, Any]] = []
    cumulative_fraction = Fraction(0)
    total_ev_fraction = Fraction(0)
    selected_repo_hours: Dict[str, Fraction] = {}
    for order, item in enumerate(selected_items, start=1):
        cumulative_fraction += item["_effort_fraction"]
        total_ev_fraction += item["_expected_value_fraction"]
        repo_key = item.get("_velocity_repo_key")
        if repo_key is not None:
            selected_repo_hours[repo_key] = (
                selected_repo_hours.get(repo_key, Fraction(0)) + item["_effort_fraction"]
            )
        repo_limit = repo_limits.get(repo_key)
        selected.append(
            {
                "allocation_order": order,
                "input_index": item["input_index"],
                "rank": item["rank"],
                "canonical_source_url": item["canonical_source_url"],
                "collision_group": item["collision_group"],
                "repository": item.get("_velocity_repo_display"),
                "cash_cycle_state": (
                    slow_repos[repo_key]["state"] if repo_key in slow_repos else None
                ),
                "repo_velocity_cap_hours": (
                    _portfolio._format_decimal(_portfolio._fraction_to_exact_decimal(repo_limit))
                    if repo_limit is not None
                    else None
                ),
                "hours_until_deadline": _portfolio._format_decimal(item["hours_until_deadline"]),
                "cumulative_allocated_hours": _portfolio._format_decimal(
                    _portfolio._fraction_to_exact_decimal(cumulative_fraction)
                ),
                "advertised_reward_usd": _portfolio._format_decimal(item["advertised_reward_usd"]),
                "estimated_win_probability": _portfolio._format_decimal(
                    item["estimated_win_probability"]
                ),
                "estimated_effort_hours": _portfolio._format_decimal(item["estimated_effort_hours"]),
                "estimated_expected_value_usd": _portfolio._format_decimal(
                    item["estimated_expected_value_usd"], metric=True
                ),
                "estimated_ev_per_hour_usd": _portfolio._format_decimal(
                    item["estimated_ev_per_hour_usd"], metric=True
                ),
                "skill_match": _portfolio._format_decimal(item["skill_match"], metric=True),
            }
        )

    repo_constraints: List[Dict[str, Any]] = []
    for key in sorted(slow_repos):
        limit = repo_limits[key]
        used = selected_repo_hours.get(key, Fraction(0))
        evidence = slow_repos[key]
        repo_constraints.append(
            {
                "repo": evidence["repo"],
                "cash_cycle_state": evidence["state"],
                "confirmed_sample_count": evidence["confirmed_sample_count"],
                "median_last_confirmed_transfer_lag_seconds": evidence[
                    "median_last_confirmed_transfer_lag_seconds"
                ],
                "capacity_cap_hours": _portfolio._format_decimal(
                    _portfolio._fraction_to_exact_decimal(limit)
                ),
                "selected_effort_hours": _portfolio._format_decimal(
                    _portfolio._fraction_to_exact_decimal(used)
                ),
            }
        )

    excluded.sort(key=lambda row: row["input_index"])
    selected_hours = _portfolio._fraction_to_exact_decimal(cumulative_fraction)
    remaining = _portfolio._fraction_to_exact_decimal(total_fraction - cumulative_fraction)
    total_ev = _portfolio._fraction_to_exact_decimal(total_ev_fraction)
    return {
        "schema": "cash-velocity-exact-selection/v1",
        "candidate_count": len(adjusted_candidates),
        "ranked_count": len(ranked),
        "selected_count": len(selected),
        "capacity_hours": _portfolio._format_decimal(capacity),
        "selected_effort_hours": _portfolio._format_decimal(selected_hours),
        "remaining_capacity_hours": _portfolio._format_decimal(remaining),
        "estimated_portfolio_expected_value_usd": _portfolio._format_decimal(total_ev, metric=True),
        "selected": selected,
        "excluded": list(ranking_excluded) + excluded,
        "repo_velocity_constraints": repo_constraints,
    }


def allocate_cash_velocity_portfolio(
    candidates: List[Dict[str, Any]],
    skills: List[str],
    capacity_hours: Any,
    closeout_manifest_items: List[Dict[str, Any]],
    payment_bindings: List[Dict[str, Any]],
    effort_log: Dict[str, Any],
    cash_cycle_receipt: Dict[str, Any],
    cash_cycle_closeout_payload: Any,
    cash_cycle_history_payload: Any,
    cash_cycle_bindings_payload: Any,
    cash_cycle_policy_payload: Any,
    velocity_policy: Dict[str, Any],
    *,
    wallet: str,
    evaluated_at: datetime,
    saturation_threshold: int = 4,
    minimum_terminal_samples: int = 2,
    max_closeout_pages: int = 10,
) -> Dict[str, Any]:
    """Allocate exact capacity with verified slow-cash repository concentration caps."""
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise CashVelocityInputError("evaluated_at must be timezone-aware")
    evaluated_at = evaluated_at.astimezone(timezone.utc)
    policy = _velocity_policy(velocity_policy)
    receipt = _validate_cycle_authority(cash_cycle_receipt)
    receipt_at = _parse_utc(receipt.get("evaluated_at"), field="cash_cycle_receipt.evaluated_at")
    if receipt_at > evaluated_at:
        raise CashVelocityInputError("cash_cycle_receipt is from the future")
    age = evaluated_at - receipt_at
    if age > timedelta(hours=policy["max_receipt_age_hours"]):
        raise CashVelocityInputError("cash_cycle_receipt exceeds max_receipt_age_hours")
    age_seconds = age.days * 86400 + age.seconds
    try:
        verified = _cycle.verify_cash_cycle_review(
            receipt,
            cash_cycle_closeout_payload,
            cash_cycle_history_payload,
            cash_cycle_bindings_payload,
            cash_cycle_policy_payload,
            verified_at=evaluated_at,
        )
    except Exception as exc:
        raise CashVelocityInputError("cash_cycle_receipt verification failed") from exc
    if verified is not True:
        raise CashVelocityInputError("cash_cycle_receipt verification failed")
    slow_repos = _slow_repositories(receipt)

    try:
        calibration = _cash.allocate_cash_calibrated_portfolio(
            candidates,
            skills,
            capacity_hours,
            closeout_manifest_items,
            payment_bindings,
            effort_log,
            wallet=wallet,
            saturation_threshold=saturation_threshold,
            minimum_terminal_samples=minimum_terminal_samples,
            max_closeout_pages=max_closeout_pages,
        )
    except Exception:
        raise
    fingerprint = calibration.get("calibration_receipt_sha256")
    if type(fingerprint) is not str or not _SHA256_RE.fullmatch(fingerprint):
        raise CashVelocityInputError("cash calibration omitted valid receipt SHA-256")
    authority = calibration.get("authority")
    if (
        type(authority) is not dict
        or authority.get("probability_can_only_decrease") is not True
        or authority.get("currency_conversion") is not False
        or authority.get("rtc_amount_used_in_usd_math") is not False
        or authority.get("claim_or_submission_authority") is not False
        or authority.get("cash_or_settlement_claim") is not False
        or authority.get("accounting_or_tax_claim") is not False
    ):
        raise CashVelocityInputError("cash calibration authority ceiling is incompatible")

    adjusted = _apply_cash_calibration(candidates, calibration.get("calibration"))
    slow_fraction = _bounded_decimal_text(
        policy["slow_repo_max_capacity_fraction"],
        field="slow_repo_max_capacity_fraction",
    )
    selection = _select_velocity_portfolio(
        adjusted,
        skills,
        capacity_hours,
        slow_repos,
        slow_fraction,
        saturation_threshold=saturation_threshold,
    )
    baseline = calibration.get("portfolio")
    if type(baseline) is not dict:
        raise CashVelocityInputError("cash calibration omitted baseline portfolio")

    payload: Dict[str, Any] = {
        "schema": _SCHEMA,
        "evaluated_at": _iso(evaluated_at),
        "velocity_policy": policy,
        "cash_cycle_receipt_sha256": receipt["receipt_sha256"],
        "cash_cycle_receipt_age_seconds": age_seconds,
        "cash_calibration_receipt_sha256": fingerprint,
        "baseline_cash_calibrated_portfolio": baseline,
        "portfolio": selection,
        "authority": {
            "eligibility": "canonical_opportunity_ranker",
            "probability_calibration": "live_cash_calibrated_portfolio",
            "cash_cycle_source": "verified_historical_receipt_and_exact_source_payloads",
            "cash_cycle_freshness": "operator_bounded_receipt_age",
            "velocity_constraint": "slow_repo_total_capacity_fraction_only",
            "opportunity_rank_metric_mutation": False,
            "advertised_reward_mutation": False,
            "currency_conversion": False,
            "rtc_amount_used_in_usd_math": False,
            "payer_or_sponsor_identity_inference": False,
            "sponsor_or_maintainer_contact": False,
            "claim_or_submission_authority": False,
            "wallet_or_provider_mutation": False,
            "payout_or_transfer_initiation": False,
            "debt_or_receivable_assertion": False,
            "cash_recognition": False,
            "revenue_recognition": False,
            "accounting_or_tax_claim": False,
        },
    }
    payload["receipt_sha256"] = _sha256(payload)
    return payload


def format_summary(result: Dict[str, Any]) -> str:
    portfolio = result.get("portfolio", {})
    baseline = result.get("baseline_cash_calibrated_portfolio", {})
    return (
        f"selected={portfolio.get('selected_count', 0)} "
        f"baseline_selected={baseline.get('selected_count', 0)} "
        f"slow_repo_caps={len(portfolio.get('repo_velocity_constraints', []))} "
        f"estimated_portfolio_ev_usd={portfolio.get('estimated_portfolio_expected_value_usd', '0')} "
        "cash_claim=false currency_conversion=false"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.cash_velocity_portfolio",
        description=(
            "Compose live cash-probability calibration with a verified cash-cycle "
            "receipt and exact slow-repository capacity caps."
        ),
    )
    parser.add_argument("request", help="request JSON: candidates, skills, capacity_hours, velocity_policy")
    parser.add_argument("manifest", help="cash calibration closeout manifest JSON")
    parser.add_argument("bindings", help="cash calibration payment bindings JSON")
    parser.add_argument("effort", help="cash calibration effort JSON")
    parser.add_argument("cycle_receipt", help="cash-cycle review receipt JSON")
    parser.add_argument("cycle_closeout", help="cash-cycle source closeout JSON")
    parser.add_argument("cycle_history", help="cash-cycle source history JSON")
    parser.add_argument("cycle_bindings", help="cash-cycle source bindings JSON")
    parser.add_argument("cycle_policy", help="cash-cycle source policy JSON")
    parser.add_argument("--wallet", required=True, help="canonical recipient wallet")
    parser.add_argument("--minimum-terminal-samples", type=int, default=2)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--max-closeout-pages", type=int, default=10)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    paths = [
        args.request,
        args.manifest,
        args.bindings,
        args.effort,
        args.cycle_receipt,
        args.cycle_closeout,
        args.cycle_history,
        args.cycle_bindings,
        args.cycle_policy,
    ]
    if paths.count("-") > 1:
        parser.error("at most one input may read from stdin")
    try:
        request = _cash._load_json(args.request)
        manifest = _cash._load_json(args.manifest)
        bindings = _cash._load_json(args.bindings)
        effort = _cash._load_json(args.effort)
        cycle_receipt = _cash._load_json(args.cycle_receipt)
        cycle_closeout = _cash._load_json(args.cycle_closeout)
        cycle_history = _cash._load_json(args.cycle_history)
        cycle_bindings = _cash._load_json(args.cycle_bindings)
        cycle_policy = _cash._load_json(args.cycle_policy)
        if type(request) is not dict:
            raise CashVelocityInputError("request must be an object")
        result = allocate_cash_velocity_portfolio(
            request.get("candidates"),
            request.get("skills", []),
            request.get("capacity_hours"),
            _cash._schema_items(manifest, "manifest"),
            _cash._schema_items(bindings, "bindings"),
            effort,
            cycle_receipt,
            cycle_closeout,
            cycle_history,
            cycle_bindings,
            cycle_policy,
            request.get("velocity_policy"),
            wallet=args.wallet,
            evaluated_at=datetime.now(timezone.utc),
            saturation_threshold=args.saturation_threshold,
            minimum_terminal_samples=args.minimum_terminal_samples,
            max_closeout_pages=args.max_closeout_pages,
        )
    except Exception as exc:
        if isinstance(exc, SystemExit):
            raise
        parser.error(str(exc))
    if args.summary:
        print(format_summary(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["portfolio"]["selected_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
