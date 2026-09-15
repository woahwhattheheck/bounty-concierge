# SPDX-License-Identifier: MIT
"""Fail-closed fleet economics admission for paid-work planning.

This module sits *after* canonical reward qualification and *before* expensive
implementation dispatch.  It does not establish that work is legitimate,
available, claimable, accepted, earned, settled, or paid.

Unlike expected-value rankers, this gate never invents a win probability and
never performs FX.  It evaluates only one native currency at a time against an
explicit operator policy:

* a standalone item must clear both a native reward floor and a native
  reward-per-agent-hour floor;
* a below-threshold item can survive only inside an explicitly compatible
  same-currency batch whose aggregate reward, aggregate reward/hour, and item
  count clear separate policy floors;
* duplicate work identities and mixed-currency batch keys fail closed.

The result is decision support only.  ``economically_eligible`` is never
external dispatch, claim, submission, payout, cash, or revenue authority.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_SCHEMA = "fleet-economic-admission/v1"
_POLICY_SCHEMA = "fleet-economic-policy/v1"
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_DECIMAL_TEXT_CHARS = 128
_MAX_DECIMAL_DIGITS = 64
_MAX_DECIMAL_ABS_EXPONENT = 64
_MAX_CANDIDATES = 10000
_MAX_JSON_BYTES = 4 * 1024 * 1024


class EconomicAdmissionInputError(ValueError):
    """Raised when economics evidence or policy is structurally unreliable."""


def _exact_decimal(value: Any, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise EconomicAdmissionInputError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise EconomicAdmissionInputError(
            f"{name} must be a decimal string, integer, or Decimal"
        )
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise EconomicAdmissionInputError(f"{name} must be non-empty")
        if len(value) > _MAX_DECIMAL_TEXT_CHARS:
            raise EconomicAdmissionInputError(
                f"{name} exceeds the supported exact-decimal representation"
            )
    elif isinstance(value, int) and value.bit_length() > 256:
        raise EconomicAdmissionInputError(
            f"{name} exceeds the supported exact-decimal representation"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise EconomicAdmissionInputError(f"{name} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise EconomicAdmissionInputError(f"{name} must be a finite decimal")
    parts = parsed.as_tuple()
    if (
        len(parts.digits) > _MAX_DECIMAL_DIGITS
        or abs(parts.exponent) > _MAX_DECIMAL_ABS_EXPONENT
    ):
        raise EconomicAdmissionInputError(
            f"{name} exceeds the supported exact-decimal representation"
        )
    if positive and parsed <= 0:
        raise EconomicAdmissionInputError(f"{name} must be greater than zero")
    if not positive and parsed < 0:
        raise EconomicAdmissionInputError(f"{name} must be non-negative")
    return parsed


def _positive_int(value: Any, name: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EconomicAdmissionInputError(f"{name} must be an integer")
    if value <= 0 or value > maximum:
        raise EconomicAdmissionInputError(
            f"{name} must be between 1 and {maximum}"
        )
    return value


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EconomicAdmissionInputError(f"{name} must be a non-empty string")
    return value.strip()


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fraction_text(value: Fraction, *, places: int = 6) -> str:
    scale = 10 ** places
    scaled = value * scale
    # Round half away from zero; all economics values are non-negative.
    rounded = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
    whole, fraction = divmod(rounded, scale)
    if not fraction:
        return str(whole)
    return f"{whole}.{fraction:0{places}d}".rstrip("0")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_policy(raw: Any) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EconomicAdmissionInputError("policy must be an object")
    if raw.get("schema") != _POLICY_SCHEMA:
        raise EconomicAdmissionInputError(
            f"policy.schema must be {_POLICY_SCHEMA!r}"
        )

    min_batch_items = _positive_int(
        raw.get("min_batch_items"), "policy.min_batch_items", maximum=_MAX_CANDIDATES
    )
    max_batch_items = _positive_int(
        raw.get("max_batch_items"), "policy.max_batch_items", maximum=_MAX_CANDIDATES
    )
    if min_batch_items > max_batch_items:
        raise EconomicAdmissionInputError(
            "policy.min_batch_items cannot exceed policy.max_batch_items"
        )

    raw_currencies = raw.get("currencies")
    if type(raw_currencies) is not dict or not raw_currencies:
        raise EconomicAdmissionInputError(
            "policy.currencies must be a non-empty object"
        )

    currencies: dict[str, dict[str, str]] = {}
    for raw_currency, raw_limits in sorted(raw_currencies.items()):
        if type(raw_currency) is not str or not _CURRENCY_RE.fullmatch(raw_currency):
            raise EconomicAdmissionInputError(
                "policy currency keys must be exact three-letter uppercase codes"
            )
        if type(raw_limits) is not dict:
            raise EconomicAdmissionInputError(
                f"policy.currencies.{raw_currency} must be an object"
            )
        allowed = {
            "min_single_reward",
            "min_batch_reward",
            "min_reward_per_agent_hour",
        }
        if set(raw_limits) != allowed:
            raise EconomicAdmissionInputError(
                f"policy.currencies.{raw_currency} must contain exactly "
                "min_single_reward, min_batch_reward, min_reward_per_agent_hour"
            )
        single = _exact_decimal(
            raw_limits["min_single_reward"],
            f"policy.currencies.{raw_currency}.min_single_reward",
            positive=True,
        )
        batch = _exact_decimal(
            raw_limits["min_batch_reward"],
            f"policy.currencies.{raw_currency}.min_batch_reward",
            positive=True,
        )
        rate = _exact_decimal(
            raw_limits["min_reward_per_agent_hour"],
            f"policy.currencies.{raw_currency}.min_reward_per_agent_hour",
            positive=True,
        )
        if batch < single:
            raise EconomicAdmissionInputError(
                f"policy.currencies.{raw_currency}.min_batch_reward "
                "cannot be below min_single_reward"
            )
        currencies[raw_currency] = {
            "min_single_reward": _format_decimal(single),
            "min_batch_reward": _format_decimal(batch),
            "min_reward_per_agent_hour": _format_decimal(rate),
        }

    return {
        "schema": _POLICY_SCHEMA,
        "min_batch_items": min_batch_items,
        "max_batch_items": max_batch_items,
        "currencies": currencies,
    }


def _normalize_candidate(
    raw: Any,
    index: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise EconomicAdmissionInputError(f"candidates[{index}] must be an object")

    work_id = _nonempty_string(raw.get("work_id"), f"candidates[{index}].work_id")
    source = _nonempty_string(
        raw.get("canonical_source_url"),
        f"candidates[{index}].canonical_source_url",
    )
    currency = raw.get("currency")
    if type(currency) is not str or not _CURRENCY_RE.fullmatch(currency):
        raise EconomicAdmissionInputError(
            f"candidates[{index}].currency must be an exact three-letter uppercase code"
        )
    if currency not in policy["currencies"]:
        raise EconomicAdmissionInputError(
            f"candidates[{index}].currency has no economics policy"
        )
    reward = _exact_decimal(
        raw.get("advertised_reward"),
        f"candidates[{index}].advertised_reward",
        positive=True,
    )
    effort = _exact_decimal(
        raw.get("estimated_agent_hours"),
        f"candidates[{index}].estimated_agent_hours",
        positive=True,
    )
    batch_key = raw.get("batch_key")
    if batch_key is not None:
        batch_key = _nonempty_string(
            batch_key, f"candidates[{index}].batch_key"
        )

    return {
        "input_index": index,
        "work_id": work_id,
        "canonical_source_url": source,
        "currency": currency,
        "advertised_reward": _format_decimal(reward),
        "estimated_agent_hours": _format_decimal(effort),
        "batch_key": batch_key,
        "_reward": reward,
        "_effort": effort,
        "_reward_fraction": Fraction(reward),
        "_effort_fraction": Fraction(effort),
    }


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    reward_rate = item["_reward_fraction"] / item["_effort_fraction"]
    return {
        "input_index": item["input_index"],
        "work_id": item["work_id"],
        "canonical_source_url": item["canonical_source_url"],
        "currency": item["currency"],
        "advertised_reward": item["advertised_reward"],
        "estimated_agent_hours": item["estimated_agent_hours"],
        "reward_per_agent_hour": _fraction_text(reward_rate),
        "batch_key": item["batch_key"],
    }


def compile_fleet_economic_admission(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a deterministic pre-dispatch economics receipt.

    ``advertised_reward`` is planning evidence only.  ``estimated_agent_hours``
    is an operator estimate, not realized labor or inference cost.  This
    compiler never converts currency and never infers probability, acceptance,
    payment, cash, or revenue.
    """
    if type(request) is not dict:
        raise EconomicAdmissionInputError("request must be an object")
    if request.get("schema") != _SCHEMA:
        raise EconomicAdmissionInputError(f"request.schema must be {_SCHEMA!r}")

    policy = _normalize_policy(request.get("policy"))
    raw_candidates = request.get("candidates")
    if type(raw_candidates) is not list:
        raise EconomicAdmissionInputError("candidates must be a list")
    if len(raw_candidates) > _MAX_CANDIDATES:
        raise EconomicAdmissionInputError(
            f"candidates cannot exceed {_MAX_CANDIDATES} items"
        )

    candidates = [
        _normalize_candidate(raw, index, policy)
        for index, raw in enumerate(raw_candidates)
    ]

    work_ids: set[str] = set()
    sources: set[str] = set()
    batch_currency: dict[str, str] = {}
    for item in candidates:
        if item["work_id"] in work_ids:
            raise EconomicAdmissionInputError(
                f"duplicate work_id: {item['work_id']}"
            )
        if item["canonical_source_url"] in sources:
            raise EconomicAdmissionInputError(
                f"duplicate canonical_source_url: {item['canonical_source_url']}"
            )
        work_ids.add(item["work_id"])
        sources.add(item["canonical_source_url"])

        batch_key = item["batch_key"]
        if batch_key is not None:
            prior = batch_currency.setdefault(batch_key, item["currency"])
            if prior != item["currency"]:
                raise EconomicAdmissionInputError(
                    f"batch_key {batch_key!r} mixes native currencies"
                )

    decisions: dict[str, dict[str, Any]] = {}
    batch_pool: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for item in candidates:
        limits = policy["currencies"][item["currency"]]
        min_single = Decimal(limits["min_single_reward"])
        min_rate = Fraction(Decimal(limits["min_reward_per_agent_hour"]))
        reward_rate = item["_reward_fraction"] / item["_effort_fraction"]

        reward_ok = item["_reward"] >= min_single
        rate_ok = reward_rate >= min_rate
        if reward_ok and rate_ok:
            decisions[item["work_id"]] = {
                **_public_candidate(item),
                "disposition": "SINGLE_ELIGIBLE",
                "economically_eligible": True,
                "reason_codes": [
                    "SINGLE_REWARD_FLOOR_MET",
                    "REWARD_RATE_FLOOR_MET",
                ],
            }
            continue

        if item["batch_key"] is not None:
            batch_pool.setdefault(
                (item["currency"], item["batch_key"]), []
            ).append(item)

        reasons = []
        if not reward_ok:
            reasons.append("BELOW_SINGLE_REWARD_FLOOR")
        if not rate_ok:
            reasons.append("BELOW_REWARD_RATE_FLOOR")
        if item["batch_key"] is None:
            reasons.append("NO_COMPATIBLE_BATCH")
        decisions[item["work_id"]] = {
            **_public_candidate(item),
            "disposition": "ECONOMIC_HOLD",
            "economically_eligible": False,
            "reason_codes": reasons,
        }

    batches: list[dict[str, Any]] = []
    for (currency, batch_key), items in sorted(batch_pool.items()):
        limits = policy["currencies"][currency]
        min_batch_reward = Decimal(limits["min_batch_reward"])
        min_rate = Fraction(Decimal(limits["min_reward_per_agent_hour"]))

        total_reward = sum(
            (item["_reward"] for item in items), Decimal("0")
        )
        total_effort = sum(
            (item["_effort"] for item in items), Decimal("0")
        )
        rate = Fraction(total_reward) / Fraction(total_effort)
        count_ok = (
            policy["min_batch_items"]
            <= len(items)
            <= policy["max_batch_items"]
        )
        reward_ok = total_reward >= min_batch_reward
        rate_ok = rate >= min_rate
        eligible = count_ok and reward_ok and rate_ok

        batch_reason_codes: list[str] = []
        if not count_ok:
            if len(items) < policy["min_batch_items"]:
                batch_reason_codes.append("BATCH_ITEM_COUNT_BELOW_FLOOR")
            else:
                batch_reason_codes.append("BATCH_ITEM_COUNT_ABOVE_LIMIT")
        if not reward_ok:
            batch_reason_codes.append("BATCH_REWARD_FLOOR_NOT_MET")
        if not rate_ok:
            batch_reason_codes.append("BATCH_REWARD_RATE_FLOOR_NOT_MET")
        if eligible:
            batch_reason_codes = [
                "BATCH_ITEM_COUNT_MET",
                "BATCH_REWARD_FLOOR_MET",
                "BATCH_REWARD_RATE_FLOOR_MET",
            ]
            for item in items:
                decisions[item["work_id"]] = {
                    **_public_candidate(item),
                    "disposition": "BATCH_ELIGIBLE",
                    "economically_eligible": True,
                    "reason_codes": ["COMPATIBLE_BATCH_ECONOMICS_MET"],
                    "economic_batch": {
                        "batch_key": batch_key,
                        "currency": currency,
                    },
                }

        batches.append(
            {
                "batch_key": batch_key,
                "currency": currency,
                "item_count": len(items),
                "work_ids": [item["work_id"] for item in items],
                "aggregate_advertised_reward": _format_decimal(total_reward),
                "aggregate_estimated_agent_hours": _format_decimal(total_effort),
                "aggregate_reward_per_agent_hour": _fraction_text(rate),
                "economically_eligible": eligible,
                "reason_codes": batch_reason_codes,
            }
        )

    ordered = [decisions[item["work_id"]] for item in candidates]
    eligible_count = sum(
        1 for item in ordered if item["economically_eligible"]
    )
    hold_count = len(ordered) - eligible_count
    policy_digest = _sha256_json(policy)

    receipt_without_digest = {
        "schema": _SCHEMA,
        "policy": policy,
        "policy_sha256": policy_digest,
        "candidate_count": len(ordered),
        "economically_eligible_count": eligible_count,
        "economic_hold_count": hold_count,
        "candidates": ordered,
        "batches": batches,
        "authority": {
            "reward": "advertised_native_currency_planning_evidence_only",
            "effort": "operator_estimate_only",
            "fx_conversion": False,
            "win_probability_inferred": False,
            "canonical_eligibility_rechecked": False,
            "dispatch_authority": False,
            "claim_or_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
        },
    }
    return {
        **receipt_without_digest,
        "receipt_sha256": _sha256_json(receipt_without_digest),
    }


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify receipt self-integrity only; this is not source/reward authority."""
    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    return _sha256_json(body) == digest


def _parse_json_object(text: str, context: str) -> dict[str, Any]:
    def reject_constant(value: str) -> Any:
        raise EconomicAdmissionInputError(
            f"{context} contains non-standard numeric constant {value}"
        )

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EconomicAdmissionInputError(
                    f"{context} contains duplicate key {key!r}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            text,
            parse_float=str,
            parse_int=int,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, EconomicAdmissionInputError):
            raise
        raise EconomicAdmissionInputError(f"{context} is not valid JSON") from exc
    if type(payload) is not dict:
        raise EconomicAdmissionInputError(f"{context} must contain an object")
    return payload


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        data = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
    else:
        file_path = Path(path)
        if file_path.is_symlink():
            raise EconomicAdmissionInputError("request path must not be a symlink")
        with file_path.open("rb") as handle:
            data = handle.read(_MAX_JSON_BYTES + 1)
    if len(data) > _MAX_JSON_BYTES:
        raise EconomicAdmissionInputError(
            f"request JSON exceeds {_MAX_JSON_BYTES} bytes"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EconomicAdmissionInputError("request JSON must be UTF-8") from exc
    return _parse_json_object(text, "request JSON")


def format_summary(receipt: dict[str, Any]) -> str:
    return (
        f"eligible={receipt['economically_eligible_count']} "
        f"hold={receipt['economic_hold_count']} "
        f"dispatch_authority=false receipt_sha256={receipt['receipt_sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.fleet_economic_admission",
        description=(
            "Apply explicit native-currency fleet economics before expensive "
            "paid-work implementation dispatch."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        request = _load_request(args.request)
        receipt = compile_fleet_economic_admission(request)
    except (OSError, EconomicAdmissionInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
