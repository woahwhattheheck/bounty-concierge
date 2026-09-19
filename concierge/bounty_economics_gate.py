# SPDX-License-Identifier: MIT
"""Fail-closed internal economics gate for bounty scheduling.

This module does not convert token rewards to USD, make FX assumptions, apply to
providers, claim work, or move funds. It only classifies source-verified cash
economics into the owner's scheduling buckets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "bounty-economics-gate/v1"
RECEIPT_SCHEMA = "bounty-economics-gate-receipt/v1"

ACTIVE_FLOOR_USD_CENTS = 5_000
PILE_FLOOR_USD_CENTS = 1_000

KINDS = {"FIXED", "RANGE", "UNPRICED", "DISCRETIONARY", "TOKEN_ONLY"}

AUTHORITY = {
    "advisory_only": True,
    "provider_application_authority": False,
    "implementation_write_authority": False,
    "submission_authority": False,
    "payment_or_wallet_authority": False,
}


class BountyEconomicsInputError(ValueError):
    """Raised when a bounty economics snapshot is malformed or contradictory."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: Any, field: str, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise BountyEconomicsInputError(f"{field} must be an object")
    keys = set(value)
    if keys != fields:
        missing = sorted(fields - keys)
        extra = sorted(keys - fields)
        raise BountyEconomicsInputError(
            f"{field} must have exact fields; missing={missing} extra={extra}"
        )
    return value


def _string(
    value: Any,
    field: str,
    *,
    allow_none: bool = False,
    max_chars: int = 2048,
) -> str | None:
    if value is None and allow_none:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise BountyEconomicsInputError(f"{field} must be a non-empty trimmed string")
    if len(value) > max_chars:
        raise BountyEconomicsInputError(f"{field} exceeds {max_chars} characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise BountyEconomicsInputError(f"{field} must not contain control characters")
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise BountyEconomicsInputError(f"{field} must be a boolean")
    return value


def _cents(value: Any, field: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if type(value) is not int:
        raise BountyEconomicsInputError(f"{field} must be an integer number of cents")
    if value < 0 or value > 10**12:
        raise BountyEconomicsInputError(f"{field} is out of range")
    return value


def _sha(value: Any, field: str) -> str:
    value = _string(value, field, max_chars=64)
    if re.fullmatch(r"[0-9a-f]{64}", value or "") is None:
        raise BountyEconomicsInputError(f"{field} must be 64 lowercase hex characters")
    return value


def _normalize_reward(value: Any) -> dict[str, Any]:
    reward = _object(
        value,
        "reward",
        {
            "kind",
            "source_ref",
            "source_sha256",
            "usd_basis_verified",
            "min_usd_cents",
            "max_usd_cents",
            "native_currency",
            "native_amount",
        },
    )
    kind = _string(reward["kind"], "reward.kind", max_chars=32).upper()
    if kind not in KINDS:
        raise BountyEconomicsInputError(
            f"reward.kind must be one of {sorted(KINDS)}"
        )

    source_ref = _string(reward["source_ref"], "reward.source_ref")
    source_sha256 = _sha(reward["source_sha256"], "reward.source_sha256")
    usd_basis_verified = _bool(
        reward["usd_basis_verified"], "reward.usd_basis_verified"
    )
    min_cents = _cents(
        reward["min_usd_cents"], "reward.min_usd_cents", allow_none=True
    )
    max_cents = _cents(
        reward["max_usd_cents"], "reward.max_usd_cents", allow_none=True
    )
    native_currency = _string(
        reward["native_currency"],
        "reward.native_currency",
        allow_none=True,
        max_chars=32,
    )
    native_amount = _string(
        reward["native_amount"],
        "reward.native_amount",
        allow_none=True,
        max_chars=128,
    )

    if kind == "FIXED":
        if min_cents is None or max_cents is None or min_cents != max_cents:
            raise BountyEconomicsInputError(
                "FIXED reward requires equal min_usd_cents and max_usd_cents"
            )
    elif kind == "RANGE":
        if min_cents is None or max_cents is None or min_cents > max_cents:
            raise BountyEconomicsInputError(
                "RANGE reward requires min_usd_cents <= max_usd_cents"
            )
    else:
        if min_cents is not None or max_cents is not None:
            raise BountyEconomicsInputError(
                f"{kind} reward must not carry USD amount fields"
            )
        if usd_basis_verified:
            raise BountyEconomicsInputError(
                f"{kind} reward cannot set usd_basis_verified=true without USD amounts"
            )

    if kind == "TOKEN_ONLY":
        if native_currency is None or native_amount is None:
            raise BountyEconomicsInputError(
                "TOKEN_ONLY reward requires native_currency and native_amount"
            )
    elif native_currency is None and native_amount is not None:
        raise BountyEconomicsInputError("native_amount requires native_currency")

    return {
        "kind": kind,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "usd_basis_verified": usd_basis_verified,
        "min_usd_cents": min_cents,
        "max_usd_cents": max_cents,
        "native_currency": native_currency,
        "native_amount": native_amount,
    }


def _bucket(cents: int) -> str:
    if cents < PILE_FLOOR_USD_CENTS:
        return "PRUNE_BELOW_10"
    if cents < ACTIVE_FLOOR_USD_CENTS:
        return "PILE_10_49"
    return "ACTIVE_50_PLUS"


def compile_economics_gate(request: dict[str, Any]) -> dict[str, Any]:
    request = _object(
        request,
        "request",
        {"schema", "opportunity_id", "program", "reward"},
    )
    if request["schema"] != SCHEMA:
        raise BountyEconomicsInputError(f"schema must equal {SCHEMA}")

    opportunity_id = _string(
        request["opportunity_id"], "opportunity_id", max_chars=256
    )
    program = _string(request["program"], "program", max_chars=256)
    reward = _normalize_reward(request["reward"])

    reasons: list[str] = []
    routing_channel: str | None = None

    if not reward["usd_basis_verified"]:
        if reward["kind"] == "TOKEN_ONLY":
            disposition = "HOLD_TOKEN_ONLY"
            reasons.append("NO_VERIFIED_USD_CASH_BASIS")
        elif reward["kind"] in {"UNPRICED", "DISCRETIONARY"}:
            disposition = "HOLD_UNPRICED"
            reasons.append("NO_VERIFIED_USD_AMOUNT")
        else:
            disposition = "HOLD_UNVERIFIED_CASH"
            reasons.append("USD_BASIS_NOT_VERIFIED")
    else:
        min_cents = reward["min_usd_cents"]
        max_cents = reward["max_usd_cents"]
        assert min_cents is not None and max_cents is not None
        min_bucket = _bucket(min_cents)
        max_bucket = _bucket(max_cents)
        if min_bucket != max_bucket:
            disposition = "HOLD_RANGE_CROSSES_BUCKET"
            reasons.append("REWARD_RANGE_CROSSES_SCHEDULING_BOUNDARY")
        else:
            disposition = min_bucket
            if disposition == "ACTIVE_50_PLUS":
                routing_channel = "#bug-bounty"
            elif disposition == "PILE_10_49":
                routing_channel = "#bounty-pile-10-49"

    allowed_actions = {
        "ACTIVE_50_PLUS": ["TAKE", "IMPLEMENT", "REVIEW", "MERGE"],
        "PILE_10_49": ["PARK_ONLY"],
        "PRUNE_BELOW_10": [],
        "HOLD_TOKEN_ONLY": ["VERIFY_ECONOMICS_ONLY"],
        "HOLD_UNPRICED": ["VERIFY_ECONOMICS_ONLY"],
        "HOLD_UNVERIFIED_CASH": ["VERIFY_ECONOMICS_ONLY"],
        "HOLD_RANGE_CROSSES_BUCKET": ["VERIFY_ECONOMICS_ONLY"],
    }[disposition]

    body = {
        "schema": RECEIPT_SCHEMA,
        "request_schema": SCHEMA,
        "identity": {
            "opportunity_id": opportunity_id,
            "program": program,
        },
        "disposition": disposition,
        "reason_codes": reasons,
        "routing": {
            "channel": routing_channel,
            "allowed_actions": allowed_actions,
            "active_floor_usd_cents": ACTIVE_FLOOR_USD_CENTS,
            "pile_floor_usd_cents": PILE_FLOOR_USD_CENTS,
        },
        "evidence": {"reward": reward},
        "authority": dict(AUTHORITY),
    }
    return {**body, "economics_receipt_sha256": _sha256(body)}


def verify_economics_receipt(receipt: dict[str, Any]) -> bool:
    if type(receipt) is not dict:
        return False
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("authority") != AUTHORITY:
        return False
    digest = receipt.get("economics_receipt_sha256")
    if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    try:
        identity = _object(
            receipt.get("identity"), "identity", {"opportunity_id", "program"}
        )
        evidence = _object(receipt.get("evidence"), "evidence", {"reward"})
        reconstructed = {
            "schema": SCHEMA,
            "opportunity_id": identity["opportunity_id"],
            "program": identity["program"],
            "reward": evidence["reward"],
        }
        expected = compile_economics_gate(reconstructed)
    except (BountyEconomicsInputError, KeyError, TypeError, ValueError):
        return False
    return expected == receipt


def format_summary(receipt: dict[str, Any]) -> str:
    identity = receipt["identity"]
    reasons = ",".join(receipt["reason_codes"]) or "none"
    channel = receipt["routing"]["channel"] or "-"
    return (
        f"economics={receipt['disposition']} "
        f"opportunity={identity['opportunity_id']} "
        f"program={identity['program']} "
        f"channel={channel} reasons={reasons} "
        f"economics_receipt_sha256={receipt['economics_receipt_sha256']}"
    )


def _load(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if type(payload) is not dict:
        raise BountyEconomicsInputError("input must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.bounty_economics_gate",
        description=(
            "Classify source-verified bounty economics into ACTIVE >=$50, "
            "$10-$49 pile, prune, or fail-closed HOLD."
        ),
    )
    parser.add_argument("snapshot", help="economics JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true", help="emit full receipt JSON")
    args = parser.parse_args(argv)

    try:
        receipt = compile_economics_gate(_load(args.snapshot))
    except (OSError, json.JSONDecodeError, BountyEconomicsInputError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(receipt, indent=2, sort_keys=True)
        if args.json
        else format_summary(receipt)
    )
    return 0 if receipt["disposition"] == "ACTIVE_50_PLUS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
