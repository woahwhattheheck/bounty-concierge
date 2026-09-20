# SPDX-License-Identifier: MIT
"""Normalize caller-supplied supply snapshots around a verified bounty-value receipt."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from concierge.bounty_value_router import verify_receipt as verify_value_receipt

SCHEMA = "bounty-supply-normalizer/v1"
RECEIPT_SCHEMA = "bounty-supply-normalizer-receipt/v1"
MAX_ITEMS = 2000
MAX_AGE = 900
ACTIVE_ASSETS = frozenset({"USD", "USDC"})
ACTIVE_FLOOR = Decimal("50")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DISPOSITIONS = (
    "SNAPSHOT_NORMALIZED_FOR_VIABILITY", "HOLD_AVAILABILITY_STALE", "HOLD_PRIMARY_STATE_UNKNOWN",
    "HOLD_SOURCE_CENSUS_INCOMPLETE", "HOLD_CARRIER_CENSUS_INCOMPLETE",
    "HOLD_VALUE_NOT_ACTIVE", "HOLD_MARKETPLACE_STATE_NOT_OPEN", "SUPPRESS_PRIMARY_CLOSED", "SUPPRESS_MAINTAINER_HOLD",
    "SUPPRESS_EXCLUSIVE_ASSIGNMENT", "SUPPRESS_EXISTING_CARRIER",
)
AUTHORITY = {
    "advisory_only": True, "claim_authority": False, "implementation_authority": False,
    "submission_authority": False, "queue_publication_authority": False,
    "payment_or_wallet_authority": False, "marketplace_override_authority": False,
    "source_or_collision_evidence_authority": False,
}
SNAPSHOT_KEYS = {
    "work_id", "canonical_source_url", "observed_at", "primary_state",
    "primary_state_reason", "maintainer_hold", "exclusive_assignment", "assignees",
    "source_census_complete", "carrier_census_complete", "active_carrier_count",
    "carrier_urls", "marketplace_advertised_open",
}


class BountySupplyGateInputError(ValueError):
    pass


def _exact_json(value: Any, field: str = "value", depth: int = 0) -> Any:
    """Copy JSON-like data while rejecting caller-owned container/scalar subclasses."""
    if depth > 100:
        raise BountySupplyGateInputError(f"{field} exceeds maximum JSON nesting depth")
    kind = type(value)
    if kind is dict:
        out: dict[str, Any] = {}
        for key, child in value.items():
            if type(key) is not str:
                raise BountySupplyGateInputError(f"{field} contains a non-string JSON object key")
            out[key] = _exact_json(child, f"{field}.{key}", depth + 1)
        return out
    if kind is list:
        return [_exact_json(child, f"{field}[{i}]", depth + 1) for i, child in enumerate(value)]
    if kind in {str, int, bool} or value is None:
        return value
    if kind is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise BountySupplyGateInputError(f"{field} contains a non-finite JSON number")
        return value
    raise BountySupplyGateInputError(f"{field} must contain only exact built-in JSON types")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise BountySupplyGateInputError(f"{field} must be an object")
    return value


def _text(value: Any, field: str, limit: int = 2048) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > limit:
        raise BountySupplyGateInputError(f"{field} must be a non-empty trimmed string <= {limit} chars")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BountySupplyGateInputError(f"{field} contains control characters")
    return value


def _utc(value: Any, field: str) -> datetime:
    raw = _text(value, field, 64)
    if not raw.endswith("Z"):
        raise BountySupplyGateInputError(f"{field} must be UTC RFC3339 ending in Z")
    try:
        result = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise BountySupplyGateInputError(f"{field} must be valid RFC3339") from exc
    if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
        raise BountySupplyGateInputError(f"{field} must be UTC")
    return result


def _str_list(value: Any, field: str, limit: int = 100) -> list[str]:
    if type(value) is not list or len(value) > limit:
        raise BountySupplyGateInputError(f"{field} must be a list with <= {limit} entries")
    out = sorted(_text(v, f"{field}[{i}]") for i, v in enumerate(value))
    if len(out) != len(set(out)):
        raise BountySupplyGateInputError(f"{field} contains duplicates")
    return out


def _snapshot(raw: Any, index: int, evaluated: datetime) -> dict[str, Any]:
    field = f"availability[{index}]"
    item = _obj(raw, field)
    if set(item) != SNAPSHOT_KEYS:
        raise BountySupplyGateInputError(f"{field} must contain exactly {sorted(SNAPSHOT_KEYS)}")
    observed_raw = _text(item["observed_at"], f"{field}.observed_at", 64)
    observed = _utc(observed_raw, f"{field}.observed_at")
    if observed > evaluated:
        raise BountySupplyGateInputError(f"{field}.observed_at must not be in the future")
    state = _text(item["primary_state"], f"{field}.primary_state", 16).upper()
    if state not in {"OPEN", "CLOSED", "UNKNOWN"}:
        raise BountySupplyGateInputError(f"{field}.primary_state must be OPEN/CLOSED/UNKNOWN")
    reason = item["primary_state_reason"]
    reason = None if reason is None else _text(reason, f"{field}.primary_state_reason", 80).upper()
    for name in ("maintainer_hold", "exclusive_assignment", "source_census_complete", "carrier_census_complete"):
        if type(item[name]) is not bool:
            raise BountySupplyGateInputError(f"{field}.{name} must be boolean")
    advertised = item["marketplace_advertised_open"]
    if advertised is not None and type(advertised) is not bool:
        raise BountySupplyGateInputError(f"{field}.marketplace_advertised_open must be boolean or null")
    count = item["active_carrier_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise BountySupplyGateInputError(f"{field}.active_carrier_count must be a non-negative integer")
    urls = _str_list(item["carrier_urls"], f"{field}.carrier_urls")
    if len(urls) > count:
        raise BountySupplyGateInputError(f"{field}.carrier_urls cannot exceed active_carrier_count")
    age = evaluated - observed
    return {
        "work_id": _text(item["work_id"], f"{field}.work_id", 200),
        "canonical_source_url": _text(item["canonical_source_url"], f"{field}.canonical_source_url"),
        "observed_at": observed_raw,
        "snapshot_age_microseconds": ((age.days * 86400 + age.seconds) * 1_000_000 + age.microseconds),
        "snapshot_within_max_age": age <= timedelta(seconds=MAX_AGE),
        "primary_state": state, "primary_state_reason": reason,
        "maintainer_hold": item["maintainer_hold"], "exclusive_assignment": item["exclusive_assignment"],
        "assignees": _str_list(item["assignees"], f"{field}.assignees"),
        "source_census_complete": item["source_census_complete"],
        "carrier_census_complete": item["carrier_census_complete"],
        "active_carrier_count": count, "carrier_urls": urls,
        "marketplace_advertised_open": advertised,
    }


def _fixed_floor(row: dict[str, Any]) -> bool:
    selected = row.get("selected_amount")
    if row.get("disposition") != "VALUE_50_PLUS" or type(selected) is not dict:
        return False
    if selected.get("asset") not in ACTIVE_ASSETS:
        return False
    amount = selected.get("amount")
    if isinstance(amount, (bool, float)) or not isinstance(amount, (str, int, Decimal)):
        return False
    try:
        amount = Decimal(amount)
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite() and amount >= ACTIVE_FLOOR


def _classify(value_row: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if snap["marketplace_advertised_open"] is True and snap["primary_state"] == "CLOSED":
        reasons.append("MARKETPLACE_OPEN_CONFLICTS_WITH_PRIMARY")
    tests = (
        (not snap["snapshot_within_max_age"], "HOLD_AVAILABILITY_STALE", "PRIMARY_AVAILABILITY_SNAPSHOT_STALE"),
        (snap["primary_state"] == "UNKNOWN", "HOLD_PRIMARY_STATE_UNKNOWN", "PRIMARY_STATE_UNKNOWN"),
        (snap["primary_state"] == "CLOSED", "SUPPRESS_PRIMARY_CLOSED", "PRIMARY_SOURCE_CLOSED"),
        (snap["maintainer_hold"], "SUPPRESS_MAINTAINER_HOLD", "MAINTAINER_STOP_OR_HOLD"),
        (snap["exclusive_assignment"], "SUPPRESS_EXCLUSIVE_ASSIGNMENT", "EXCLUSIVE_ASSIGNMENT_PRESENT"),
        (not snap["source_census_complete"], "HOLD_SOURCE_CENSUS_INCOMPLETE", "SOURCE_CENSUS_INCOMPLETE"),
        (snap["active_carrier_count"] > 0, "SUPPRESS_EXISTING_CARRIER", "ACTIVE_OR_MERGED_CARRIER_PRESENT"),
        (not snap["carrier_census_complete"], "HOLD_CARRIER_CENSUS_INCOMPLETE", "CARRIER_CENSUS_INCOMPLETE"),
        (not _fixed_floor(value_row), "HOLD_VALUE_NOT_ACTIVE", "FIXED_50_USD_USDC_FLOOR_NOT_MET"),
        (snap["marketplace_advertised_open"] is not True, "HOLD_MARKETPLACE_STATE_NOT_OPEN", "MARKETPLACE_STATE_CLOSED_OR_UNKNOWN_REQUIRES_DOWNSTREAM_RECHECK"),
    )
    disposition = "SNAPSHOT_NORMALIZED_FOR_VIABILITY"
    for matched, candidate, reason in tests:
        if matched:
            disposition = candidate
            reasons.append(reason)
            break
    if disposition == "SNAPSHOT_NORMALIZED_FOR_VIABILITY":
        reasons.append("VERIFIED_VALUE_AND_CALLER_SNAPSHOT_NORMALIZED")
    return {
        "work_id": value_row["work_id"], "canonical_source_url": value_row["canonical_source_url"],
        "disposition": disposition,
        "recommended_route": "bounty-canonical-viability" if disposition == "SNAPSHOT_NORMALIZED_FOR_VIABILITY" else None,
        "reason_codes": reasons, "selected_amount": value_row.get("selected_amount"),
        "value_disposition": value_row.get("disposition"), "availability": snap,
    }


def compile_bounty_supply_gate(request: dict[str, Any]) -> dict[str, Any]:
    request = _obj(_exact_json(request, "request"), "request")
    if set(request) != {"schema", "evaluated_at", "value_receipt", "availability"}:
        raise BountySupplyGateInputError("request must contain exactly schema/evaluated_at/value_receipt/availability")
    if request["schema"] != SCHEMA:
        raise BountySupplyGateInputError(f"schema must equal {SCHEMA}")
    value_receipt = request["value_receipt"]
    if not verify_value_receipt(value_receipt):
        raise BountySupplyGateInputError("value_receipt failed semantic verification")
    evaluated_raw = _text(request["evaluated_at"], "evaluated_at", 64)
    evaluated = _utc(evaluated_raw, "evaluated_at")
    rows = value_receipt.get("candidates")
    if type(rows) is not list or not rows or len(rows) > MAX_ITEMS:
        raise BountySupplyGateInputError(f"value_receipt candidates must be a non-empty list <= {MAX_ITEMS}")
    value_input = value_receipt.get("input")
    if type(value_input) is not dict or value_input.get("evaluated_at") != evaluated_raw:
        raise BountySupplyGateInputError("value_receipt evaluated_at must exactly match supply-gate evaluated_at")
    availability = request["availability"]
    if type(availability) is not list or len(availability) > MAX_ITEMS:
        raise BountySupplyGateInputError(f"availability must be a list <= {MAX_ITEMS}")
    snaps = [_snapshot(raw, i, evaluated) for i, raw in enumerate(availability)]
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snaps:
        key = (snap["work_id"], snap["canonical_source_url"])
        if key in by_key:
            raise BountySupplyGateInputError("availability contains duplicate work_id/source pairs")
        by_key[key] = snap
    expected: set[tuple[str, str]] = set()
    result = []
    for i, row in enumerate(rows):
        if type(row) is not dict or type(row.get("work_id")) is not str or type(row.get("canonical_source_url")) is not str:
            raise BountySupplyGateInputError(f"value_receipt.candidates[{i}] lacks canonical identity")
        key = (row["work_id"], row["canonical_source_url"])
        if key in expected:
            raise BountySupplyGateInputError("value_receipt contains duplicate canonical identity")
        expected.add(key)
        if key not in by_key:
            raise BountySupplyGateInputError(f"missing availability snapshot for {key[0]} {key[1]}")
        result.append(_classify(row, by_key[key]))
    if set(by_key) - expected:
        raise BountySupplyGateInputError("availability contains candidates absent from value_receipt")
    result.sort(key=lambda r: (r["work_id"], r["canonical_source_url"]))
    snaps.sort(key=lambda r: (r["work_id"], r["canonical_source_url"]))
    normalized = {
        "schema": SCHEMA, "evaluated_at": evaluated_raw, "value_receipt": value_receipt,
        "availability": [{k: s[k] for k in SNAPSHOT_KEYS} for s in snaps],
    }
    counts = {name: sum(r["disposition"] == name for r in result) for name in DISPOSITIONS}
    body = {
        "schema": RECEIPT_SCHEMA, "input": normalized, "max_snapshot_age_seconds": MAX_AGE,
        "counts": counts, "candidates": result,
        "routing_rule": "normalization only: verified fixed >=50 USD/USDC value is identity-bound to caller snapshots; negative/stale/unknown signals hold or suppress; downstream canonical viability must independently verify source, provider, assignment, and collision state",
        "trust_model": {
            "value_receipt": "SEMANTICALLY_VERIFIED_EXACT_JSON",
            "availability": "CALLER_ASSERTED_UNVERIFIED_ORCHESTRATOR_SNAPSHOT",
            "downstream_canonical_reverification_required": True,
        },
        "authority": dict(AUTHORITY),
    }
    return {**body, "receipt_sha256": _hash(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    try:
        receipt = _exact_json(receipt, "receipt")
    except BountySupplyGateInputError:
        return False
    if type(receipt) is not dict or receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("authority") != AUTHORITY:
        return False
    if receipt.get("max_snapshot_age_seconds") != MAX_AGE or not SHA_RE.fullmatch(str(receipt.get("receipt_sha256", ""))):
        return False
    try:
        return compile_bounty_supply_gate(receipt.get("input")) == receipt
    except (BountySupplyGateInputError, KeyError, TypeError, ValueError):
        return False


def _strict_loads(text: str) -> Any:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise BountySupplyGateInputError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    def constant(value):
        raise BountySupplyGateInputError(f"non-finite JSON constant: {value}")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise BountySupplyGateInputError(str(exc)) from exc


def _load(path: str) -> dict[str, Any]:
    if path == "-":
        import sys
        return _obj(_strict_loads(sys.stdin.read()), "request")
    return _obj(_strict_loads(Path(path).read_text(encoding="utf-8")), "request")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.bounty_supply_gate", description="Normalize caller-asserted supply snapshots around a verified bounty-value receipt")
    parser.add_argument("request")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_bounty_supply_gate(_load(args.request))
    except (OSError, BountySupplyGateInputError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        c = receipt["counts"]
        print(f"normalized={c['SNAPSHOT_NORMALIZED_FOR_VIABILITY']} held={sum(v for k,v in c.items() if k.startswith('HOLD_'))} suppressed={sum(v for k,v in c.items() if k.startswith('SUPPRESS_'))} receipt_sha256={receipt['receipt_sha256']}")
    return 0 if receipt["counts"]["SNAPSHOT_NORMALIZED_FOR_VIABILITY"] == len(receipt["candidates"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
