# SPDX-License-Identifier: MIT
"""Evidence-bound paid-work effort/value admission.

Composes ``fleet_economic_admission`` with operational facts that determine
whether paid work is worth spending engineering capacity on now.  This module
does not claim work, send submissions, prove payment, perform FX, or assign a
cash value to project tokens/credits.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Optional

from concierge import fleet_economic_admission as fleet_economics


_REQUEST_SCHEMA = "paid-work-effort-value-gate/v1"
_POLICY_SCHEMA = "paid-work-effort-value-policy/v1"
_RECEIPT_SCHEMA = "paid-work-effort-value-gate-receipt/v1"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_WORK_ID_CHARS = 256
_MAX_REASON_COUNT = 64
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")
_ALLOWED_DECISIONS = frozenset(
    {"GO", "HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE", "SKIP_ECONOMICS"}
)


class PaidWorkGateInputError(ValueError):
    """Malformed or internally inconsistent paid-work admission input."""


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
        raise PaidWorkGateInputError("value is not canonical JSON") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise PaidWorkGateInputError("canonical JSON value is too large")
    return encoded


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _object(
    value: Any,
    field: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if type(value) is not dict:
        raise PaidWorkGateInputError(f"{field} must be an object")
    keys = frozenset(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise PaidWorkGateInputError(
            f"{field} is missing required field {missing[0]!r}"
        )
    if unknown:
        raise PaidWorkGateInputError(
            f"{field} contains unsupported field {unknown[0]!r}"
        )
    return value


def _bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaidWorkGateInputError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise PaidWorkGateInputError(f"{field} must be in {minimum}..{maximum}")
    return value


def _decimal(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PaidWorkGateInputError(
            f"{field} must be an exact decimal string or integer"
        )
    source = str(value)
    if len(source) > 64:
        raise PaidWorkGateInputError(f"{field} representation is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise PaidWorkGateInputError(f"{field} must be a decimal") from exc
    exponent = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or len(amount.as_tuple().digits) > 30
        or not isinstance(exponent, int)
        or abs(exponent) > 18
        or (positive and amount <= 0)
        or (nonnegative and amount < 0)
    ):
        qualifier = (
            "positive " if positive else "non-negative " if nonnegative else ""
        )
        raise PaidWorkGateInputError(
            f"{field} must be a bounded {qualifier}decimal"
        )
    return amount


def _amount_text(amount: Decimal) -> str:
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise PaidWorkGateInputError(
            f"{field} must be an exact UTC timestamp like 2026-09-16T20:00:00Z"
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PaidWorkGateInputError(
            f"{field} is not a valid UTC timestamp"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _freshness(
    observed_at: Any,
    *,
    as_of: datetime,
    max_age_seconds: int,
    field: str,
) -> tuple[str, bool, int]:
    observed = _timestamp(observed_at, field)
    if observed > as_of:
        raise PaidWorkGateInputError(f"{field} must not be in the future")
    age = int((as_of - observed).total_seconds())
    return str(observed_at), age <= max_age_seconds, age


def _currency(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CURRENCY_RE.fullmatch(value) is None:
        raise PaidWorkGateInputError(
            f"{field} must be an uppercase currency/unit identifier"
        )
    return value


def _evidence_gate(
    raw: Any,
    field: str,
    *,
    allowed_states: frozenset[str],
    as_of: datetime,
    max_age_seconds: int,
    extra_required: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = frozenset({"state", "evidence_url", "observed_at"}) | extra_required
    value = _object(raw, field, required=required)
    state = value["state"]
    if state not in allowed_states:
        raise PaidWorkGateInputError(
            f"{field}.state must be one of {sorted(allowed_states)}"
        )
    try:
        evidence_identity = fleet_economics._source_identity(
            value["evidence_url"], f"{field}.evidence_url"
        )
    except fleet_economics.EconomicAdmissionInputError as exc:
        raise PaidWorkGateInputError(str(exc)) from exc
    observed_at, fresh, age = _freshness(
        value["observed_at"],
        as_of=as_of,
        max_age_seconds=max_age_seconds,
        field=f"{field}.observed_at",
    )
    normalized = {
        "state": state,
        "evidence_url": value["evidence_url"],
        "evidence_identity": evidence_identity,
        "observed_at": observed_at,
        "age_seconds": age,
        "fresh": fresh,
    }
    for key in sorted(extra_required):
        normalized[key] = value[key]
    return value, normalized


def _strict_json_bytes(payload: bytes, source: str) -> Any:
    if len(payload) > _MAX_JSON_BYTES:
        raise PaidWorkGateInputError(f"{source} is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise PaidWorkGateInputError(f"{source} is not UTF-8") from exc

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PaidWorkGateInputError(
                    f"{source} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_float=Decimal,
            parse_int=int,
        )
    except PaidWorkGateInputError:
        raise
    except json.JSONDecodeError as exc:
        raise PaidWorkGateInputError(f"{source} is not valid JSON") from exc


def _load_request(path: str) -> Any:
    if path == "-":
        payload = sys.stdin.buffer.read(_MAX_JSON_BYTES + 1)
        return _strict_json_bytes(payload, "stdin")
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_JSON_BYTES:
            raise PaidWorkGateInputError(f"{path} is too large")
        return _strict_json_bytes(source.read_bytes(), path)
    except PaidWorkGateInputError:
        raise
    except OSError as exc:
        raise PaidWorkGateInputError(f"cannot read {path}") from exc


def _normalize_policy(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = _object(
        raw,
        "policy",
        required=frozenset(
            {
                "schema",
                "evidence_max_age_seconds",
                "deadline_safety_seconds",
                "max_active_claims",
                "fleet_economic_policy",
            }
        ),
    )
    if policy["schema"] != _POLICY_SCHEMA:
        raise PaidWorkGateInputError(f"policy.schema must be {_POLICY_SCHEMA!r}")
    evidence_max_age_seconds = _bounded_int(
        policy["evidence_max_age_seconds"],
        "policy.evidence_max_age_seconds",
        minimum=1,
        maximum=31 * 24 * 60 * 60,
    )
    deadline_safety_seconds = _bounded_int(
        policy["deadline_safety_seconds"],
        "policy.deadline_safety_seconds",
        minimum=0,
        maximum=31 * 24 * 60 * 60,
    )
    max_active_claims = _bounded_int(
        policy["max_active_claims"],
        "policy.max_active_claims",
        minimum=1,
        maximum=1000,
    )
    fleet_policy = policy["fleet_economic_policy"]
    if (
        type(fleet_policy) is not dict
        or fleet_policy.get("schema") != "fleet-economic-policy/v1"
    ):
        raise PaidWorkGateInputError(
            "policy.fleet_economic_policy must use fleet-economic-policy/v1"
        )
    return policy, {
        "evidence_max_age_seconds": evidence_max_age_seconds,
        "deadline_safety_seconds": deadline_safety_seconds,
        "max_active_claims": max_active_claims,
        "fleet_economic_policy": fleet_policy,
    }


def _currency_floor(
    fleet_policy: dict[str, Any],
    currency: str,
) -> tuple[Decimal, Decimal]:
    currencies = fleet_policy.get("currencies")
    if type(currencies) is not dict:
        raise PaidWorkGateInputError(
            "policy.fleet_economic_policy.currencies must be an object"
        )
    currency_policy = currencies.get(currency)
    if type(currency_policy) is not dict:
        raise PaidWorkGateInputError(
            f"policy.fleet_economic_policy has no policy for {currency}"
        )
    return (
        _decimal(
            currency_policy.get("min_single_reward"),
            f"policy.fleet_economic_policy.currencies.{currency}.min_single_reward",
            positive=True,
        ),
        _decimal(
            currency_policy.get("min_reward_per_agent_hour"),
            f"policy.fleet_economic_policy.currencies.{currency}.min_reward_per_agent_hour",
            positive=True,
        ),
    )


def _compile_gross_economics(
    *,
    work_id: str,
    source_url: str,
    currency: str,
    payout: Decimal,
    hours: Decimal,
    policy: dict[str, Any],
) -> dict[str, Any]:
    request = {
        "schema": "fleet-economic-admission/v1",
        "policy": policy,
        "candidates": [
            {
                "work_id": work_id,
                "canonical_source_url": source_url,
                "currency": currency,
                "advertised_reward": _amount_text(payout),
                "estimated_agent_hours": _amount_text(hours),
                "batch_key": None,
            }
        ],
    }
    try:
        receipt = fleet_economics.compile_fleet_economic_admission(request)
    except fleet_economics.EconomicAdmissionInputError as exc:
        raise PaidWorkGateInputError(
            f"fleet economics rejected candidate: {exc}"
        ) from exc
    candidates = receipt.get("candidates")
    if type(candidates) is not list or len(candidates) != 1:
        raise PaidWorkGateInputError(
            "fleet economics returned an unexpected candidate scope"
        )
    return receipt


def compile_paid_work_effort_value_gate(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a deterministic paid-work admission receipt."""

    top = _object(
        request,
        "request",
        required=frozenset({"schema", "as_of", "policy", "candidate"}),
    )
    if top["schema"] != _REQUEST_SCHEMA:
        raise PaidWorkGateInputError(f"request.schema must be {_REQUEST_SCHEMA!r}")

    as_of = _timestamp(top["as_of"], "as_of")
    raw_policy, policy = _normalize_policy(top["policy"])
    candidate = _object(
        top["candidate"],
        "candidate",
        required=frozenset(
            {
                "work_id",
                "canonical_source_url",
                "advertised_payout",
                "estimated_engineering_hours",
                "model_tool_cost",
                "deadline_at",
                "congestion",
                "acceptance",
                "payout_route",
                "account_kyc",
            }
        ),
    )

    work_id = candidate["work_id"]
    if (
        not isinstance(work_id, str)
        or not work_id
        or work_id != work_id.strip()
        or len(work_id) > _MAX_WORK_ID_CHARS
        or any(not character.isprintable() for character in work_id)
    ):
        raise PaidWorkGateInputError(
            "candidate.work_id must be a bounded printable non-empty string"
        )

    source_url = candidate["canonical_source_url"]
    try:
        source_identity = fleet_economics._source_identity(
            source_url, "candidate.canonical_source_url"
        )
    except fleet_economics.EconomicAdmissionInputError as exc:
        raise PaidWorkGateInputError(str(exc)) from exc

    evidence_max_age = policy["evidence_max_age_seconds"]
    payout_raw = _object(
        candidate["advertised_payout"],
        "candidate.advertised_payout",
        required=frozenset({"amount", "currency", "unit_type", "observed_at"}),
    )
    payout = _decimal(
        payout_raw["amount"],
        "candidate.advertised_payout.amount",
        positive=True,
    )
    payout_currency = _currency(
        payout_raw["currency"], "candidate.advertised_payout.currency"
    )
    unit_type = payout_raw["unit_type"]
    if unit_type not in {"CASH", "NONCASH"}:
        raise PaidWorkGateInputError(
            "candidate.advertised_payout.unit_type must be CASH or NONCASH"
        )
    payout_observed_at, payout_fresh, payout_age = _freshness(
        payout_raw["observed_at"],
        as_of=as_of,
        max_age_seconds=evidence_max_age,
        field="candidate.advertised_payout.observed_at",
    )

    hours = _decimal(
        candidate["estimated_engineering_hours"],
        "candidate.estimated_engineering_hours",
        positive=True,
    )

    cost_raw = _object(
        candidate["model_tool_cost"],
        "candidate.model_tool_cost",
        required=frozenset({"state"}),
        optional=frozenset({"amount", "currency"}),
    )
    cost_state = cost_raw["state"]
    if cost_state not in {"KNOWN", "UNKNOWN"}:
        raise PaidWorkGateInputError(
            "candidate.model_tool_cost.state must be KNOWN or UNKNOWN"
        )
    tool_cost: Optional[Decimal] = None
    tool_cost_currency: Optional[str] = None
    if cost_state == "KNOWN":
        if "amount" not in cost_raw or "currency" not in cost_raw:
            raise PaidWorkGateInputError(
                "known candidate.model_tool_cost requires amount and currency"
            )
        tool_cost = _decimal(
            cost_raw["amount"],
            "candidate.model_tool_cost.amount",
            nonnegative=True,
        )
        tool_cost_currency = _currency(
            cost_raw["currency"], "candidate.model_tool_cost.currency"
        )
    elif "amount" in cost_raw or "currency" in cost_raw:
        raise PaidWorkGateInputError(
            "unknown candidate.model_tool_cost must not assert amount or currency"
        )

    deadline_at = _timestamp(candidate["deadline_at"], "candidate.deadline_at")
    deadline_remaining = int((deadline_at - as_of).total_seconds())

    congestion_raw = _object(
        candidate["congestion"],
        "candidate.congestion",
        required=frozenset({"active_claims", "observed_at"}),
    )
    active_claims = _bounded_int(
        congestion_raw["active_claims"],
        "candidate.congestion.active_claims",
        minimum=0,
        maximum=1000,
    )
    congestion_observed_at, congestion_fresh, congestion_age = _freshness(
        congestion_raw["observed_at"],
        as_of=as_of,
        max_age_seconds=evidence_max_age,
        field="candidate.congestion.observed_at",
    )

    acceptance_raw, acceptance = _evidence_gate(
        candidate["acceptance"],
        "candidate.acceptance",
        allowed_states=frozenset({"CONFIRMED", "UNKNOWN", "REJECTED"}),
        as_of=as_of,
        max_age_seconds=evidence_max_age,
        extra_required=frozenset({"authority"}),
    )
    if acceptance_raw["authority"] not in {"FIRST_PARTY", "OTHER", "UNKNOWN"}:
        raise PaidWorkGateInputError(
            "candidate.acceptance.authority must be FIRST_PARTY, OTHER, or UNKNOWN"
        )

    _, payout_route = _evidence_gate(
        candidate["payout_route"],
        "candidate.payout_route",
        allowed_states=frozenset({"CONFIRMED", "UNKNOWN", "BLOCKED"}),
        as_of=as_of,
        max_age_seconds=evidence_max_age,
    )
    _, account_kyc = _evidence_gate(
        candidate["account_kyc"],
        "candidate.account_kyc",
        allowed_states=frozenset({"READY", "NOT_REQUIRED", "UNKNOWN", "BLOCKED"}),
        as_of=as_of,
        max_age_seconds=evidence_max_age,
    )

    skip_reasons: list[str] = []
    value_reasons: list[str] = []
    account_reasons: list[str] = []

    if deadline_remaining <= 0:
        skip_reasons.append("DEADLINE_EXPIRED")
    elif deadline_remaining < policy["deadline_safety_seconds"]:
        skip_reasons.append("DEADLINE_SAFETY_WINDOW_NOT_MET")

    if not congestion_fresh:
        account_reasons.append("CONGESTION_EVIDENCE_STALE")
    elif active_claims >= policy["max_active_claims"]:
        skip_reasons.append("CLAIM_CONGESTION_SATURATED")

    if not payout_fresh:
        value_reasons.append("PAYOUT_EVIDENCE_STALE")
    if unit_type != "CASH":
        value_reasons.append("NONCASH_VALUE_UNAUTHORIZED")
    if cost_state != "KNOWN":
        value_reasons.append("MODEL_TOOL_COST_UNKNOWN")
    elif tool_cost_currency != payout_currency:
        value_reasons.append("MODEL_TOOL_COST_CURRENCY_MISMATCH_NO_FX")

    if acceptance["state"] != "CONFIRMED":
        account_reasons.append("ACCEPTANCE_NOT_CONFIRMED")
    if acceptance_raw["authority"] != "FIRST_PARTY":
        account_reasons.append("ACCEPTANCE_NOT_FIRST_PARTY")
    if not acceptance["fresh"]:
        account_reasons.append("ACCEPTANCE_EVIDENCE_STALE")

    if payout_route["state"] != "CONFIRMED":
        account_reasons.append("PAYOUT_ROUTE_NOT_CONFIRMED")
    if not payout_route["fresh"]:
        account_reasons.append("PAYOUT_ROUTE_EVIDENCE_STALE")

    if account_kyc["state"] not in {"READY", "NOT_REQUIRED"}:
        account_reasons.append("ACCOUNT_KYC_NOT_READY")
    if not account_kyc["fresh"]:
        account_reasons.append("ACCOUNT_KYC_EVIDENCE_STALE")

    gross_receipt: Optional[dict[str, Any]] = None
    gross_eligible: Optional[bool] = None
    net_reward: Optional[Decimal] = None
    net_rate: Optional[str] = None
    single_floor: Optional[Decimal] = None
    rate_floor: Optional[Decimal] = None

    # Never invent economics when the value domain is unknown. Deadline and
    # fresh congestion terminal reasons still win because they need no FX/value.
    if not value_reasons:
        gross_receipt = _compile_gross_economics(
            work_id=work_id,
            source_url=source_url,
            currency=payout_currency,
            payout=payout,
            hours=hours,
            policy=policy["fleet_economic_policy"],
        )
        row = gross_receipt["candidates"][0]
        gross_eligible = row.get("economically_eligible") is True
        if not gross_eligible:
            skip_reasons.append("GROSS_ECONOMICS_INELIGIBLE")
        else:
            if tool_cost is None:
                raise PaidWorkGateInputError(
                    "internal value guard lost known model/tool cost"
                )
            net_reward = payout - tool_cost
            if net_reward <= 0:
                skip_reasons.append("NONPOSITIVE_NET_REWARD")
            else:
                single_floor, rate_floor = _currency_floor(
                    policy["fleet_economic_policy"], payout_currency
                )
                if net_reward < single_floor:
                    skip_reasons.append("POST_COST_REWARD_FLOOR_NOT_MET")
                with localcontext() as context:
                    context.prec = 64
                    net_per_hour = net_reward / hours
                net_rate = _amount_text(net_per_hour)
                if net_per_hour < rate_floor:
                    skip_reasons.append("POST_COST_REWARD_RATE_FLOOR_NOT_MET")

    if skip_reasons:
        decision = "SKIP_ECONOMICS"
        reason_codes = skip_reasons
        observed_holds = value_reasons + account_reasons
    elif value_reasons:
        decision = "HOLD_VALUE_UNKNOWN"
        reason_codes = value_reasons
        observed_holds = account_reasons
    elif account_reasons:
        decision = "HOLD_ACCOUNT_GATE"
        reason_codes = account_reasons
        observed_holds = []
    else:
        decision = "GO"
        reason_codes = ["ALL_GATES_CLEAR"]
        observed_holds = []

    if decision not in _ALLOWED_DECISIONS:
        raise PaidWorkGateInputError("internal decision escaped the allowed set")
    if len(reason_codes) + len(observed_holds) > _MAX_REASON_COUNT:
        raise PaidWorkGateInputError("too many reason codes")

    body = {
        "schema": _RECEIPT_SCHEMA,
        "request_sha256": _sha256_json(request),
        "policy_sha256": _sha256_json(raw_policy),
        "work_id": work_id,
        "canonical_source_url": source_url,
        "canonical_source_identity": source_identity,
        "as_of": top["as_of"],
        "decision": decision,
        "reason_codes": reason_codes,
        "observed_hold_reasons": observed_holds,
        "economics": {
            "advertised_payout": _amount_text(payout),
            "currency": payout_currency,
            "unit_type": unit_type,
            "estimated_engineering_hours": _amount_text(hours),
            "model_tool_cost_state": cost_state,
            "model_tool_cost": (
                _amount_text(tool_cost) if tool_cost is not None else None
            ),
            "model_tool_cost_currency": tool_cost_currency,
            "net_reward_after_model_tool_cost": (
                _amount_text(net_reward) if net_reward is not None else None
            ),
            "net_reward_per_engineering_hour": net_rate,
            "applied_min_single_reward": (
                _amount_text(single_floor) if single_floor is not None else None
            ),
            "applied_min_reward_per_agent_hour": (
                _amount_text(rate_floor) if rate_floor is not None else None
            ),
            "gross_economically_eligible": gross_eligible,
            "fleet_economic_receipt": gross_receipt,
        },
        "gates": {
            "payout_value": {
                "observed_at": payout_observed_at,
                "age_seconds": payout_age,
                "fresh": payout_fresh,
            },
            "deadline": {
                "deadline_at": candidate["deadline_at"],
                "seconds_remaining": deadline_remaining,
                "safety_seconds": policy["deadline_safety_seconds"],
                "viable": (
                    deadline_remaining > 0
                    and deadline_remaining >= policy["deadline_safety_seconds"]
                ),
            },
            "congestion": {
                "active_claims": active_claims,
                "max_active_claims": policy["max_active_claims"],
                "observed_at": congestion_observed_at,
                "age_seconds": congestion_age,
                "fresh": congestion_fresh,
                "saturated": (
                    congestion_fresh
                    and active_claims >= policy["max_active_claims"]
                ),
            },
            "acceptance": acceptance,
            "payout_route": payout_route,
            "account_kyc": account_kyc,
        },
        "authority": {
            "go_is_internal_admission_signal": True,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
            "fx_conversion": False,
            "noncash_valuation": False,
        },
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify outer self-integrity and any nested fleet-economics receipt."""

    if type(receipt) is not dict:
        return False
    digest = receipt.get("receipt_sha256")
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or receipt.get("schema") != _RECEIPT_SCHEMA
        or receipt.get("decision") not in _ALLOWED_DECISIONS
    ):
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    try:
        if _sha256_json(body) != digest:
            return False
    except PaidWorkGateInputError:
        return False
    economics = receipt.get("economics")
    if type(economics) is not dict:
        return False
    nested = economics.get("fleet_economic_receipt")
    if nested is not None and not fleet_economics.verify_receipt(nested):
        return False
    return True


def format_summary(receipt: dict[str, Any]) -> str:
    reasons = ",".join(receipt["reason_codes"])
    return (
        f"decision={receipt['decision']} work_id={receipt['work_id']} "
        f"reasons={reasons} receipt_sha256={receipt['receipt_sha256']}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.paid_work_effort_value_gate",
        description=(
            "Gate paid engineering work on value certainty, payout readiness, "
            "freshness, congestion, deadline, and post-cost economics."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        request = _load_request(args.request)
        receipt = compile_paid_work_effort_value_gate(request)
    except (OSError, PaidWorkGateInputError) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print(format_summary(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
