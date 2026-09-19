# SPDX-License-Identifier: MIT
"""Verified-dollar floor routing for new paid-work intake.

This compiler applies the owner's source-owned 10/50 USD routing floor before
deeper effort/value, claimability, implementation, or payout gates. Amount
semantics are explicit: FIXED values, bounded RANGE values, and UP_TO ceilings
are not interchangeable. Production policy/authority values are captured in a
private immutable generation at import; public mirrors remain diagnostics only.
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


_REQUEST_SCHEMA = "paid-work-dollar-floor/v2"
_POLICY_SCHEMA = "paid-work-dollar-floor-policy/v1"
_RECEIPT_SCHEMA = "paid-work-dollar-floor-receipt/v2"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT = 2048
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DECISIONS = frozenset(
    {
        "ACTIVE_REVIEW",
        "PILE_SAVE_UP",
        "PRUNE_BELOW_FLOOR",
        "HOLD_VERIFY_AMOUNT",
        "HOLD_VALUE_NONCOMPARABLE",
    }
)

# Diagnostic compatibility mirrors only. The production compiler/verifier below
# does not read these after its generation is built.
_ALLOWED_AUTHORITIES = frozenset({"FIRST_PARTY", "SETTLEMENT_PLATFORM"})
_SOURCE_POLICY = {
    "schema": _POLICY_SCHEMA,
    "currency": "USD",
    "pile_floor": "10",
    "active_floor": "50",
    "max_evidence_age_seconds": 86400,
    "pile_route": "bounty-pile-10-49",
    "active_route": "main-bounty-queue",
}
_AUTHORITY = {
    "advisory_only": True,
    "external_claim_authority": False,
    "external_submission_authority": False,
    "outbound_contact_authority": False,
    "payment_cash_or_revenue_authority": False,
    "fx_conversion": False,
    "noncash_valuation": False,
    "ceiling_promotes_active": False,
    "range_max_promotes_active": False,
}


class DollarFloorInputError(ValueError):
    """Malformed or internally inconsistent dollar-floor evidence."""


def _canonical_json(value: Any) -> bytes:
    try:
        raw_text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        raw = raw_text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
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
        ord(ch) < 0x20
        or ord(ch) == 0x7F
        or 0xD800 <= ord(ch) <= 0xDFFF
        for ch in value
    ):
        raise DollarFloorInputError(
            f"{field} is not a bounded printable Unicode string"
        )
    return value


def _exact_decimal(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
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
        or (nonnegative and amount < 0)
    ):
        qualifier = (
            "positive " if positive else "non-negative " if nonnegative else ""
        )
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


def _strict_https_url(
    value: Any, field: str, *, allow_fragment: bool = False
) -> str:
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
        or parsed.query
    ):
        raise DollarFloorInputError(
            f"{field} must not contain userinfo/port/query"
        )
    if parsed.fragment and not allow_fragment:
        raise DollarFloorInputError(f"{field} must not contain a fragment")
    return raw


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

    def reject_float(value: str) -> Any:
        raise DollarFloorInputError(
            f"{source} contains a floating-point number"
        )

    def reject_constant(value: str) -> Any:
        raise DollarFloorInputError(
            f"{source} contains unsupported numeric constant {value}"
        )

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique,
            parse_float=reject_float,
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


def _build_generation():
    """Capture production policy/authority in private immutable closure state."""

    policy_items = (
        ("schema", _POLICY_SCHEMA),
        ("currency", "USD"),
        ("pile_floor", "10"),
        ("active_floor", "50"),
        ("max_evidence_age_seconds", 86400),
        ("pile_route", "bounty-pile-10-49"),
        ("active_route", "main-bounty-queue"),
    )
    allowed_authorities = frozenset(
        {"FIRST_PARTY", "SETTLEMENT_PLATFORM"}
    )
    authority_items = (
        ("advisory_only", True),
        ("external_claim_authority", False),
        ("external_submission_authority", False),
        ("outbound_contact_authority", False),
        ("payment_cash_or_revenue_authority", False),
        ("fx_conversion", False),
        ("noncash_valuation", False),
        ("ceiling_promotes_active", False),
        ("range_max_promotes_active", False),
    )
    decisions = frozenset(_DECISIONS)

    def source_policy() -> dict[str, Any]:
        return dict(policy_items)

    def authority() -> dict[str, Any]:
        return dict(authority_items)

    def normalize_policy(raw: Any) -> dict[str, Any]:
        expected = source_policy()
        policy = _object(
            raw,
            "policy",
            required=frozenset(expected),
        )
        if set(policy) != set(expected):
            raise DollarFloorInputError(
                "policy must contain exactly the source-owned fields"
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
        normalized = {
            "schema": _POLICY_SCHEMA,
            "currency": "USD",
            "pile_floor": _amount_text(pile_floor),
            "active_floor": _amount_text(active_floor),
            "max_evidence_age_seconds": age,
            "pile_route": _text(
                policy["pile_route"], "policy.pile_route", max_chars=128
            ),
            "active_route": _text(
                policy["active_route"], "policy.active_route", max_chars=128
            ),
        }
        if normalized != expected:
            raise DollarFloorInputError(
                "policy must equal the source-owned paid-work dollar floor"
            )
        return normalized

    def normalize_verified_amount(
        evidence: dict[str, Any],
    ) -> tuple[str, str | None, str | None, str | None, str]:
        semantics = evidence.get("amount_semantics")
        if semantics not in {"FIXED", "RANGE", "UP_TO"}:
            raise DollarFloorInputError(
                "candidate.payout_evidence.amount_semantics must be "
                "FIXED, RANGE, or UP_TO"
            )
        present = {
            key for key in ("amount", "min_amount", "max_amount")
            if key in evidence
        }
        amount_text = None
        min_text = None
        max_text = None

        if semantics == "FIXED":
            if present != {"amount"}:
                raise DollarFloorInputError(
                    "FIXED payout evidence must contain amount only"
                )
            amount = _exact_decimal(
                evidence["amount"],
                "candidate.payout_evidence.amount",
                positive=True,
            )
            amount_text = _amount_text(amount)
            guaranteed_text = amount_text
        elif semantics == "RANGE":
            if present != {"min_amount", "max_amount"}:
                raise DollarFloorInputError(
                    "RANGE payout evidence must contain min_amount and max_amount only"
                )
            minimum = _exact_decimal(
                evidence["min_amount"],
                "candidate.payout_evidence.min_amount",
                nonnegative=True,
            )
            maximum = _exact_decimal(
                evidence["max_amount"],
                "candidate.payout_evidence.max_amount",
                positive=True,
            )
            if minimum > maximum:
                raise DollarFloorInputError(
                    "candidate.payout_evidence.min_amount must not exceed max_amount"
                )
            min_text = _amount_text(minimum)
            max_text = _amount_text(maximum)
            guaranteed_text = min_text
        else:
            if present != {"max_amount"}:
                raise DollarFloorInputError(
                    "UP_TO payout evidence must contain max_amount only"
                )
            maximum = _exact_decimal(
                evidence["max_amount"],
                "candidate.payout_evidence.max_amount",
                positive=True,
            )
            max_text = _amount_text(maximum)
            guaranteed_text = "0"

        return semantics, amount_text, min_text, max_text, guaranteed_text

    def compile_receipt(request: dict[str, Any]) -> dict[str, Any]:
        top = _object(
            request,
            "request",
            required=frozenset({"schema", "evaluated_at", "candidate"}),
            optional=frozenset({"policy"}),
        )
        if top["schema"] != _REQUEST_SCHEMA:
            raise DollarFloorInputError(
                f"request.schema must be {_REQUEST_SCHEMA!r}"
            )
        policy = (
            normalize_policy(top["policy"])
            if "policy" in top
            else source_policy()
        )
        evaluated = _timestamp(top["evaluated_at"], "evaluated_at")
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
                {
                    "amount_semantics",
                    "amount",
                    "min_amount",
                    "max_amount",
                    "currency",
                    "unit_type",
                    "authority",
                }
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
            allow_fragment=True,
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

        semantics = None
        amount_text = None
        min_text = None
        max_text = None
        guaranteed_text = None
        currency = None
        unit_type = None
        evidence_authority = None
        reasons: list[str] = []
        route = "none"

        if state == "UNVERIFIED":
            if any(
                key in evidence
                for key in (
                    "amount_semantics",
                    "amount",
                    "min_amount",
                    "max_amount",
                    "currency",
                    "unit_type",
                    "authority",
                )
            ):
                raise DollarFloorInputError(
                    "UNVERIFIED payout evidence must not assert "
                    "amount semantics/value/currency/unit_type/authority"
                )
            decision = "HOLD_VERIFY_AMOUNT"
            reasons.append("PAYOUT_AMOUNT_UNVERIFIED")
        else:
            required_verified = {
                "amount_semantics", "currency", "unit_type", "authority"
            }
            missing = sorted(required_verified - set(evidence))
            if missing:
                raise DollarFloorInputError(
                    f"VERIFIED payout evidence is missing {missing[0]!r}"
                )
            (
                semantics,
                amount_text,
                min_text,
                max_text,
                guaranteed_text,
            ) = normalize_verified_amount(evidence)
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
            if evidence_authority not in allowed_authorities | {"OTHER"}:
                raise DollarFloorInputError(
                    "candidate.payout_evidence.authority must be FIRST_PARTY, "
                    "SETTLEMENT_PLATFORM, or OTHER"
                )

            if not fresh:
                decision = "HOLD_VERIFY_AMOUNT"
                reasons.append("PAYOUT_EVIDENCE_STALE")
            elif evidence_authority not in allowed_authorities:
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
                if semantics == "FIXED":
                    value = Decimal(amount_text)
                    if value >= active_floor:
                        decision = "ACTIVE_REVIEW"
                        route = policy["active_route"]
                        reasons.append("ACTIVE_DOLLAR_FLOOR_MET")
                    elif value >= pile_floor:
                        decision = "PILE_SAVE_UP"
                        route = policy["pile_route"]
                        reasons.append("PILE_DOLLAR_BAND")
                    else:
                        decision = "PRUNE_BELOW_FLOOR"
                        route = "discard"
                        reasons.append("BELOW_MINIMUM_LABOR_FLOOR")
                elif semantics == "RANGE":
                    minimum = Decimal(min_text)
                    maximum = Decimal(max_text)
                    if minimum >= active_floor:
                        decision = "ACTIVE_REVIEW"
                        route = policy["active_route"]
                        reasons.append("GUARANTEED_ACTIVE_DOLLAR_FLOOR_MET")
                    elif minimum >= pile_floor and maximum < active_floor:
                        decision = "PILE_SAVE_UP"
                        route = policy["pile_route"]
                        reasons.append("GUARANTEED_PILE_DOLLAR_BAND")
                    elif maximum < pile_floor:
                        decision = "PRUNE_BELOW_FLOOR"
                        route = "discard"
                        reasons.append("ENTIRE_RANGE_BELOW_MINIMUM_LABOR_FLOOR")
                    else:
                        decision = "HOLD_VERIFY_AMOUNT"
                        reasons.append(
                            "PAYOUT_RANGE_CROSSES_ROUTING_BOUNDARY"
                        )
                else:
                    decision = "HOLD_VERIFY_AMOUNT"
                    reasons.append("PAYOUT_CEILING_NOT_GUARANTEED")

        if decision not in decisions:
            raise AssertionError("internal dollar-floor decision error")

        normalized_input = {
            "schema": _REQUEST_SCHEMA,
            "evaluated_at": top["evaluated_at"],
            "policy": policy,
            "candidate": {
                "work_id": work_id,
                "canonical_source_url": source_url,
                "payout_evidence": {
                    "state": state,
                    "evidence_url": evidence_url,
                    "observed_at": evidence["observed_at"],
                },
            },
        }
        if state == "VERIFIED":
            normalized_evidence = normalized_input["candidate"]["payout_evidence"]
            normalized_evidence.update(
                {
                    "amount_semantics": semantics,
                    "currency": currency,
                    "unit_type": unit_type,
                    "authority": evidence_authority,
                }
            )
            if semantics == "FIXED":
                normalized_evidence["amount"] = amount_text
            elif semantics == "RANGE":
                normalized_evidence["min_amount"] = min_text
                normalized_evidence["max_amount"] = max_text
            else:
                normalized_evidence["max_amount"] = max_text

        body = {
            "schema": _RECEIPT_SCHEMA,
            "input": normalized_input,
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
                "amount_semantics": semantics,
                "amount": amount_text,
                "min_amount": min_text,
                "max_amount": max_text,
                "guaranteed_amount": guaranteed_text,
                "currency": currency,
                "unit_type": unit_type,
                "authority": evidence_authority,
            },
            "authority": authority(),
        }
        return {**body, "receipt_sha256": _sha256_json(body)}

    def verify_receipt(receipt: dict[str, Any]) -> bool:
        if type(receipt) is not dict:
            return False
        if receipt.get("schema") != _RECEIPT_SCHEMA:
            return False
        if receipt.get("decision") not in decisions:
            return False
        if receipt.get("authority") != authority():
            return False
        digest = receipt.get("receipt_sha256")
        if (
            type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            return False
        try:
            body = dict(receipt)
            body.pop("receipt_sha256", None)
            if _sha256_json(body) != digest:
                return False
            if receipt.get("policy") != source_policy():
                return False
            if receipt.get("policy_sha256") != _sha256_json(source_policy()):
                return False
            replayed = compile_receipt(receipt.get("input"))
        except (DollarFloorInputError, TypeError, ValueError):
            return False
        return replayed == receipt

    return compile_receipt, verify_receipt, source_policy


compile_paid_work_dollar_floor, verify_receipt, get_source_policy = _build_generation()
del _build_generation


def format_summary(receipt: dict[str, Any]) -> str:
    evidence = receipt["payout_evidence"]
    guaranteed = evidence["guaranteed_amount"]
    label = guaranteed if guaranteed is not None else "unverified"
    currency = evidence["currency"] or ""
    return (
        f"decision={receipt['decision']} route={receipt['route']} "
        f"guaranteed={label}{(' ' + currency) if currency else ''} "
        f"work_id={receipt['work_id']} "
        f"receipt_sha256={receipt['receipt_sha256']}"
    )


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
