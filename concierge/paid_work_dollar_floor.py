# SPDX-License-Identifier: MIT
"""Verified-dollar floor routing for new paid-work intake.

This compiler is intentionally narrower than the downstream effort/value gate:
it decides only whether a freshly evidenced payout is large enough to enter the
active queue, belongs in the low-dollar save-up pile, must be pruned, or cannot
yet be compared. It never performs FX, values noncash units, claims work,
submits work, contacts sponsors, or establishes payment/revenue.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit


_REQUEST_SCHEMA = "paid-work-dollar-floor/v1"
_POLICY_SCHEMA = "paid-work-dollar-floor-policy/v1"
_RECEIPT_SCHEMA = "paid-work-dollar-floor-receipt/v1"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT = 2048
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_ALLOWED_AUTHORITIES = frozenset({"FIRST_PARTY", "SETTLEMENT_PLATFORM"})
_DECISIONS = frozenset(
    {
        "ACTIVE_REVIEW",
        "PILE_SAVE_UP",
        "PRUNE_BELOW_FLOOR",
        "HOLD_VERIFY_AMOUNT",
        "HOLD_VALUE_NONCOMPARABLE",
    }
)
_AUTHORITY = {
    "advisory_only": True,
    "external_claim_authority": False,
    "external_submission_authority": False,
    "outbound_contact_authority": False,
    "payment_cash_or_revenue_authority": False,
    "fx_conversion": False,
    "noncash_valuation": False,
}


class DollarFloorInputError(ValueError):
    """Malformed or internally inconsistent dollar-floor evidence."""


def _canonical_json(value: Any) -> bytes:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DollarFloorInputError("value is not canonical JSON") from exc
    if len(raw) > _MAX_JSON_BYTES:
        raise DollarFloorInputError("canonical JSON value is too large")
    return raw


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
        raise DollarFloorInputError(f"{field} must be an object")
    keys = frozenset(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise DollarFloorInputError(
            f"{field} is missing required field {missing[0]!r}"
        )
    if unknown:
        raise DollarFloorInputError(
            f"{field} contains unsupported field {unknown[0]!r}"
        )
    return value


def _text(value: Any, field: str, *, max_chars: int = _MAX_TEXT) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise DollarFloorInputError(f"{field} must be a non-empty trimmed string")
    if len(value) > max_chars or any(
        ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value
    ):
        raise DollarFloorInputError(
            f"{field} is not a bounded printable string"
        )
    return value


def _exact_decimal(
    value: Any, field: str, *, positive: bool = False
) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise DollarFloorInputError(
            f"{field} must be an exact decimal string or integer"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise DollarFloorInputError(
            f"{field} must be an exact decimal string or integer"
        )
    source = str(value)
    if len(source) > 64:
        raise DollarFloorInputError(f"{field} is too large")
    try:
        amount = Decimal(source)
    except (InvalidOperation, ValueError) as exc:
        raise DollarFloorInputError(
            f"{field} must be a finite decimal"
        ) from exc
    parts = amount.as_tuple()
    if (
        not amount.is_finite()
        or len(parts.digits) > 30
        or abs(parts.exponent) > 18
        or (positive and amount <= 0)
        or (not positive and amount < 0)
    ):
        qualifier = "positive " if positive else "non-negative "
        raise DollarFloorInputError(
            f"{field} must be a bounded {qualifier}decimal"
        )
    return amount


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value: Any, field: str) -> datetime:
    raw = _text(value, field, max_chars=32)
    if _TIMESTAMP_RE.fullmatch(raw) is None:
        raise DollarFloorInputError(
            f"{field} must be an exact UTC timestamp like "
            "2026-09-19T23:00:00Z"
        )
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise DollarFloorInputError(
            f"{field} is not a valid UTC timestamp"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _strict_https_url(value: Any, field: str) -> str:
    raw = _text(value, field, max_chars=2048)
    if any(ch.isspace() for ch in raw) or "\\" in raw:
        raise DollarFloorInputError(
            f"{field} must not contain whitespace/backslashes"
        )
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise DollarFloorInputError(
            f"{field} must be a valid URL"
        ) from exc
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise DollarFloorInputError(f"{field} must be an https URL")
    if (
        parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise DollarFloorInputError(
            f"{field} must not contain userinfo or an explicit port"
        )
    if parsed.fragment:
        raise DollarFloorInputError(f"{field} must not contain a fragment")
    return raw


def _normalize_policy(raw: Any) -> dict[str, Any]:
    policy = _object(
        raw,
        "policy",
        required=frozenset(
            {
                "schema",
                "currency",
                "pile_floor",
                "active_floor",
                "max_evidence_age_seconds",
                "pile_route",
                "active_route",
            }
        ),
    )
    if policy["schema"] != _POLICY_SCHEMA:
        raise DollarFloorInputError(
            f"policy.schema must be {_POLICY_SCHEMA!r}"
        )
    if policy["currency"] != "USD":
        raise DollarFloorInputError(
            "policy.currency must be 'USD'; this gate performs no FX"
        )
    pile_floor = _exact_decimal(
        policy["pile_floor"], "policy.pile_floor", positive=True
    )
    active_floor = _exact_decimal(
        policy["active_floor"], "policy.active_floor", positive=True
    )
    if active_floor <= pile_floor:
        raise DollarFloorInputError(
            "policy.active_floor must be greater than policy.pile_floor"
        )
    age = policy["max_evidence_age_seconds"]
    if (
        isinstance(age, bool)
        or not isinstance(age, int)
        or age < 1
        or age > 31 * 24 * 60 * 60
    ):
        raise DollarFloorInputError(
            "policy.max_evidence_age_seconds must be an integer in 1..2678400"
        )
    pile_route = _text(
        policy["pile_route"], "policy.pile_route", max_chars=128
    )
    active_route = _text(
        policy["active_route"], "policy.active_route", max_chars=128
    )
    return {
        "schema": _POLICY_SCHEMA,
        "currency": "USD",
        "pile_floor": _amount_text(pile_floor),
        "active_floor": _amount_text(active_floor),
        "max_evidence_age_seconds": age,
        "pile_route": pile_route,
        "active_route": active_route,
    }


def compile_paid_work_dollar_floor(
    request: dict[str, Any]
) -> dict[str, Any]:
    """Compile one verified-dollar routing receipt."""
    top = _object(
        request,
        "request",
        required=frozenset(
            {"schema", "evaluated_at", "policy", "candidate"}
        ),
    )
    if top["schema"] != _REQUEST_SCHEMA:
        raise DollarFloorInputError(
            f"request.schema must be {_REQUEST_SCHEMA!r}"
        )
    evaluated = _timestamp(top["evaluated_at"], "evaluated_at")
    policy = _normalize_policy(top["policy"])
    candidate = _object(
        top["candidate"],
        "candidate",
        required=frozenset(
            {"work_id", "canonical_source_url", "payout_evidence"}
        ),
    )
    work_id = _text(
        candidate["work_id"], "candidate.work_id", max_chars=256
    )
    source_url = _strict_https_url(
        candidate["canonical_source_url"],
        "candidate.canonical_source_url",
    )
    evidence = _object(
        candidate["payout_evidence"],
        "candidate.payout_evidence",
        required=frozenset({"state", "evidence_url", "observed_at"}),
        optional=frozenset(
            {"amount", "currency", "unit_type", "authority"}
        ),
    )
    state = evidence["state"]
    if state not in {"VERIFIED", "UNVERIFIED"}:
        raise DollarFloorInputError(
            "candidate.payout_evidence.state must be VERIFIED or UNVERIFIED"
        )
    evidence_url = _strict_https_url(
        evidence["evidence_url"],
        "candidate.payout_evidence.evidence_url",
    )
    observed = _timestamp(
        evidence["observed_at"],
        "candidate.payout_evidence.observed_at",
    )
    if observed > evaluated:
        raise DollarFloorInputError(
            "candidate.payout_evidence.observed_at must not be in the future"
        )
    age_seconds = int((evaluated - observed).total_seconds())
    fresh = age_seconds <= policy["max_evidence_age_seconds"]

    amount_text = None
    currency = None
    unit_type = None
    evidence_authority = None
    reasons: list[str] = []
    route = "none"

    if state == "UNVERIFIED":
        if any(
            key in evidence
            for key in ("amount", "currency", "unit_type", "authority")
        ):
            raise DollarFloorInputError(
                "UNVERIFIED payout evidence must not assert "
                "amount/currency/unit_type/authority"
            )
        decision = "HOLD_VERIFY_AMOUNT"
        reasons.append("PAYOUT_AMOUNT_UNVERIFIED")
    else:
        required_verified = {"amount", "currency", "unit_type", "authority"}
        missing = sorted(required_verified - set(evidence))
        if missing:
            raise DollarFloorInputError(
                f"VERIFIED payout evidence is missing {missing[0]!r}"
            )
        amount = _exact_decimal(
            evidence["amount"],
            "candidate.payout_evidence.amount",
            positive=True,
        )
        amount_text = _amount_text(amount)
        currency = evidence["currency"]
        unit_type = evidence["unit_type"]
        evidence_authority = evidence["authority"]
        if (
            type(currency) is not str
            or _CURRENCY_RE.fullmatch(currency) is None
        ):
            raise DollarFloorInputError(
                "candidate.payout_evidence.currency must be a "
                "three-letter uppercase code"
            )
        if unit_type not in {"CASH", "NONCASH"}:
            raise DollarFloorInputError(
                "candidate.payout_evidence.unit_type must be CASH or NONCASH"
            )
        if evidence_authority not in _ALLOWED_AUTHORITIES | {"OTHER"}:
            raise DollarFloorInputError(
                "candidate.payout_evidence.authority must be FIRST_PARTY, "
                "SETTLEMENT_PLATFORM, or OTHER"
            )

        if not fresh:
            decision = "HOLD_VERIFY_AMOUNT"
            reasons.append("PAYOUT_EVIDENCE_STALE")
        elif evidence_authority not in _ALLOWED_AUTHORITIES:
            decision = "HOLD_VERIFY_AMOUNT"
            reasons.append("PAYOUT_AUTHORITY_NOT_ACCEPTED")
        elif unit_type != "CASH" or currency != policy["currency"]:
            decision = "HOLD_VALUE_NONCOMPARABLE"
            if unit_type != "CASH":
                reasons.append("NONCASH_VALUE_UNAUTHORIZED")
            if currency != policy["currency"]:
                reasons.append("CURRENCY_REQUIRES_AUTHORIZED_FX")
        else:
            pile_floor = Decimal(policy["pile_floor"])
            active_floor = Decimal(policy["active_floor"])
            if amount >= active_floor:
                decision = "ACTIVE_REVIEW"
                route = policy["active_route"]
                reasons.append("ACTIVE_DOLLAR_FLOOR_MET")
            elif amount >= pile_floor:
                decision = "PILE_SAVE_UP"
                route = policy["pile_route"]
                reasons.append("PILE_DOLLAR_BAND")
            else:
                decision = "PRUNE_BELOW_FLOOR"
                route = "discard"
                reasons.append("BELOW_MINIMUM_LABOR_FLOOR")

    if decision not in _DECISIONS:
        raise AssertionError("internal dollar-floor decision error")

    body = {
        "schema": _RECEIPT_SCHEMA,
        "work_id": work_id,
        "canonical_source_url": source_url,
        "evaluated_at": top["evaluated_at"],
        "decision": decision,
        "route": route,
        "reason_codes": reasons,
        "policy": policy,
        "policy_sha256": _sha256_json(policy),
        "payout_evidence": {
            "state": state,
            "evidence_url": evidence_url,
            "observed_at": evidence["observed_at"],
            "age_seconds": age_seconds,
            "fresh": fresh,
            "amount": amount_text,
            "currency": currency,
            "unit_type": unit_type,
            "authority": evidence_authority,
        },
        "authority": _AUTHORITY,
    }
    return {**body, "receipt_sha256": _sha256_json(body)}


def verify_receipt(receipt: dict[str, Any]) -> bool:
    """Verify receipt integrity and the advisory authority ceiling."""
    if type(receipt) is not dict:
        return False
    if receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
    if receipt.get("decision") not in _DECISIONS:
        return False
    if receipt.get("authority") != _AUTHORITY:
        return False
    digest = receipt.get("receipt_sha256")
    if (
        type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        return False
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    if _sha256_json(body) != digest:
        return False
    policy = body.get("policy")
    if type(policy) is not dict:
        return False
    return body.get("policy_sha256") == _sha256_json(policy)


def format_summary(receipt: dict[str, Any]) -> str:
    amount = receipt["payout_evidence"]["amount"] or "unverified"
    currency = receipt["payout_evidence"]["currency"] or ""
    return (
        f"decision={receipt['decision']} route={receipt['route']} "
        f"payout={amount}{(' ' + currency) if currency else ''} "
        f"work_id={receipt['work_id']} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


def _strict_json_bytes(payload: bytes, source: str) -> dict[str, Any]:
    if len(payload) > _MAX_JSON_BYTES:
        raise DollarFloorInputError(
            f"{source} exceeds {_MAX_JSON_BYTES} bytes"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DollarFloorInputError(
            f"{source} must be UTF-8"
        ) from exc

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise DollarFloorInputError(
                    f"{source} contains duplicate JSON key {key!r}"
                )
            out[key] = value
        return out

    def reject_constant(value: str) -> Any:
        raise DollarFloorInputError(
            f"{source} contains unsupported numeric constant {value}"
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_float=str,
            parse_int=int,
            parse_constant=reject_constant,
        )
    except DollarFloorInputError:
        raise
    except json.JSONDecodeError as exc:
        raise DollarFloorInputError(
            f"{source} is not valid JSON"
        ) from exc
    if type(value) is not dict:
        raise DollarFloorInputError(f"{source} must contain an object")
    return value


def _load_request(path: str) -> dict[str, Any]:
    if path == "-":
        return _strict_json_bytes(
            sys.stdin.buffer.read(_MAX_JSON_BYTES + 1), "stdin"
        )
    source = Path(path)
    if source.is_symlink():
        raise DollarFloorInputError("request path must not be a symlink")
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise DollarFloorInputError(f"cannot read {path}") from exc
    return _strict_json_bytes(data, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.paid_work_dollar_floor",
        description=(
            "Route verified USD paid work before the deeper effort/value gate."
        ),
    )
    parser.add_argument("request", help="request JSON path, or - for stdin")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = compile_paid_work_dollar_floor(
            _load_request(args.request)
        )
    except DollarFloorInputError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    return 0 if receipt["decision"] == "ACTIVE_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
