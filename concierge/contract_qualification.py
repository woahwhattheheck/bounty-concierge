# SPDX-License-Identifier: MIT
"""Fail-closed authority for external paid-contract qualification.

Consumes a trusted normalized marketplace observation and attributable screening
claim evidence. Never browses/submits, accepts terms, spends, completes KYC,
accepts a contract, infers an award/payment, or recognizes revenue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "bounty-concierge.external-contract-qualification/v1"
RECEIPT_VERSION = "bounty-concierge.external-contract-qualification-receipt/v1"
_LISTING_TTL = 30 * 60
_AVAILABILITY_TTL = 24 * 60 * 60
_HEX64 = frozenset("0123456789abcdef")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_CLAIM_TYPES = frozenset({"EXPERIENCE", "TOOL", "AVAILABILITY", "PORTFOLIO", "RATE", "OTHER"})
_EVIDENCE_TYPES = frozenset({
    "MERGED_PR", "REPO_COMMIT", "PROVIDER_RECEIPT", "PORTFOLIO_ARTIFACT",
    "OWNER_ATTESTATION", "CURRENT_AVAILABILITY",
})
_ALLOWED = {
    "EXPERIENCE": frozenset({"MERGED_PR", "REPO_COMMIT", "PROVIDER_RECEIPT", "OWNER_ATTESTATION"}),
    "TOOL": frozenset({"MERGED_PR", "REPO_COMMIT", "PROVIDER_RECEIPT", "OWNER_ATTESTATION"}),
    "AVAILABILITY": frozenset({"CURRENT_AVAILABILITY", "OWNER_ATTESTATION"}),
    "PORTFOLIO": frozenset({"PORTFOLIO_ARTIFACT", "MERGED_PR", "REPO_COMMIT"}),
    "RATE": frozenset({"OWNER_ATTESTATION"}),
    "OTHER": _EVIDENCE_TYPES,
}
_ROUTES = frozenset({"MANUAL_OWNER", "CONNECTED_ACTION"})
_KYC = frozenset({"NOT_REQUIRED", "SATISFIED", "UNSATISFIED", "UNKNOWN"})
_STATES = frozenset({"OPEN", "CLOSED", "UNKNOWN"})


class ContractQualificationInputError(ValueError):
    """Input or receipt is not structurally trustworthy."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _obj(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ContractQualificationInputError(f"{name} must be an object")
    return value


def _keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContractQualificationInputError(
            f"{name} shape mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )


def _text(value: Any, name: str, limit: int = 512) -> str:
    if type(value) is not str or not value.strip():
        raise ContractQualificationInputError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise ContractQualificationInputError(f"{name} is too long")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ContractQualificationInputError(f"{name} contains control characters")
    return value


def _identifier(value: Any, name: str, limit: int) -> str:
    value = _text(value, name, limit)
    if not _ID_RE.fullmatch(value):
        raise ContractQualificationInputError(f"{name} must be a log-safe identifier")
    return value


def _flag(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ContractQualificationInputError(f"{name} must be boolean")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name, 64)
    if len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ContractQualificationInputError(f"{name} must be a lowercase sha256")
    return value


def _time(value: Any, name: str) -> tuple[str, int]:
    value = _text(value, name, 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractQualificationInputError(f"{name} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ContractQualificationInputError(f"{name} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if value != canonical:
        raise ContractQualificationInputError(f"{name} must be canonical second-precision UTC")
    return canonical, int(parsed.timestamp())


def trusted_utc_now() -> str:
    """Return verifier-owned process time for production currentness checks."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _amount(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
        raise ContractQualificationInputError(f"{name} must be an exact decimal string or integer")
    if isinstance(value, str) and (not value.strip() or len(value.strip()) > 64):
        raise ContractQualificationInputError(f"{name} is invalid")
    try:
        parsed = Decimal(value.strip() if isinstance(value, str) else value)
    except (InvalidOperation, ValueError) as exc:
        raise ContractQualificationInputError(f"{name} is invalid") from exc
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed.as_tuple().exponent < -2
        or parsed.adjusted() > 18
    ):
        raise ContractQualificationInputError(
            f"{name} must be finite, non-negative, <=2 decimals, and <=1e18 magnitude"
        )
    return parsed


def _amount_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _currency(value: Any, name: str) -> str:
    value = _text(value, name, 3)
    if len(value) != 3 or not value.isalpha() or value != value.upper():
        raise ContractQualificationInputError(f"{name} must be an ISO-style currency code")
    return value


def _url(value: Any, name: str) -> str:
    value = _text(value, name, 2048)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ContractQualificationInputError(f"{name} must be a fragment-free https URL")
    return value


def _listing(raw: Any, now: int) -> tuple[dict[str, Any], list[str], bool]:
    row = _obj(raw, "listing")
    _keys(row, {"url", "state", "observed_at", "closes_at", "source_digest",
                "currency", "min_amount", "max_amount"}, "listing")
    state = _text(row["state"], "listing.state", 16).upper()
    if state not in _STATES:
        raise ContractQualificationInputError("listing.state is unknown")
    observed_at, observed = _time(row["observed_at"], "listing.observed_at")
    if observed > now:
        raise ContractQualificationInputError("listing observation is from the future")
    closes_at, closes = None, None
    if row["closes_at"] is not None:
        closes_at, closes = _time(row["closes_at"], "listing.closes_at")
    minimum, maximum = _amount(row["min_amount"], "listing.min_amount"), _amount(row["max_amount"], "listing.max_amount")
    if minimum > maximum:
        raise ContractQualificationInputError("listing amount range is inverted")
    normalized = {
        "url": _url(row["url"], "listing.url"), "state": state,
        "observed_at": observed_at, "closes_at": closes_at,
        "source_digest": _digest(row["source_digest"], "listing.source_digest"),
        "currency": _currency(row["currency"], "listing.currency"),
        "min_amount": _amount_text(minimum), "max_amount": _amount_text(maximum),
    }
    reasons, rejected = [], False
    if state == "CLOSED":
        reasons.append("LISTING_CLOSED"); rejected = True
    elif state == "UNKNOWN":
        reasons.append("LISTING_STATE_UNKNOWN")
    if now - observed > _LISTING_TTL:
        reasons.append("LISTING_OBSERVATION_STALE")
    if closes is not None and closes <= now:
        reasons.append("LISTING_DEADLINE_PASSED"); rejected = True
    return normalized, reasons, rejected


def _claims(raw: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if type(raw) is not list:
        raise ContractQualificationInputError("required_claims must be a list")
    if len(raw) > 100:
        raise ContractQualificationInputError("required_claims exceeds supported boundary")
    rows, by_id = [], {}
    for index, item in enumerate(raw):
        row = _obj(item, f"required_claims[{index}]")
        _keys(row, {"claim_id", "claim_type", "mandatory"}, f"required_claims[{index}]")
        claim_id = _identifier(row["claim_id"], f"required_claims[{index}].claim_id", 80)
        if claim_id in by_id:
            raise ContractQualificationInputError(f"duplicate claim_id: {claim_id}")
        claim_type = _text(row["claim_type"], f"required_claims[{index}].claim_type", 24).upper()
        if claim_type not in _CLAIM_TYPES:
            raise ContractQualificationInputError(f"unsupported claim_type: {claim_type}")
        normalized = {"claim_id": claim_id, "claim_type": claim_type,
                      "mandatory": _flag(row["mandatory"], f"required_claims[{index}].mandatory")}
        rows.append(normalized); by_id[claim_id] = normalized
    rows.sort(key=lambda row: row["claim_id"])
    return rows, by_id


def _evidence(raw: Any, claims: dict[str, dict[str, Any]], now: int):
    if type(raw) is not list:
        raise ContractQualificationInputError("evidence must be a list")
    if len(raw) > 500:
        raise ContractQualificationInputError("evidence exceeds supported boundary")
    normalized, usable = [], {claim_id: [] for claim_id in claims}
    issues = {claim_id: set() for claim_id in claims}
    seen = set()
    for index, item in enumerate(raw):
        row = _obj(item, f"evidence[{index}]")
        _keys(row, {"evidence_id", "claim_id", "evidence_type", "evidence_ref",
                    "evidence_digest", "observed_at", "scope"}, f"evidence[{index}]")
        evidence_id = _identifier(row["evidence_id"], f"evidence[{index}].evidence_id", 96)
        if evidence_id in seen:
            raise ContractQualificationInputError(f"duplicate evidence_id: {evidence_id}")
        seen.add(evidence_id)
        claim_id = _identifier(row["claim_id"], f"evidence[{index}].claim_id", 80)
        if claim_id not in claims:
            raise ContractQualificationInputError(f"evidence[{index}] references unknown claim_id")
        kind = _text(row["evidence_type"], f"evidence[{index}].evidence_type", 32).upper()
        if kind not in _EVIDENCE_TYPES:
            raise ContractQualificationInputError("unsupported evidence_type")
        scope = _text(row["scope"], f"evidence[{index}].scope", 16).upper()
        if scope not in {"EXACT", "ADJACENT"}:
            raise ContractQualificationInputError("evidence.scope must be EXACT or ADJACENT")
        observed_at, observed = _time(row["observed_at"], f"evidence[{index}].observed_at")
        if observed > now:
            raise ContractQualificationInputError("evidence observation is from the future")
        normalized_row = {
            "evidence_id": evidence_id, "claim_id": claim_id, "evidence_type": kind,
            "evidence_ref": _text(row["evidence_ref"], f"evidence[{index}].evidence_ref", 2048),
            "evidence_digest": _digest(row["evidence_digest"], f"evidence[{index}].evidence_digest"),
            "observed_at": observed_at, "scope": scope,
        }
        normalized.append(normalized_row)
        claim = claims[claim_id]
        if scope != "EXACT":
            issues[claim_id].add(f"ADJACENT_EVIDENCE:{claim_id}")
        elif kind not in _ALLOWED[claim["claim_type"]]:
            issues[claim_id].add(f"EVIDENCE_TYPE_MISMATCH:{claim_id}")
        elif claim["claim_type"] == "AVAILABILITY" and now - observed > _AVAILABILITY_TTL:
            issues[claim_id].add(f"AVAILABILITY_EVIDENCE_STALE:{claim_id}")
        else:
            usable[claim_id].append(normalized_row)
    normalized.sort(key=lambda row: (row["claim_id"], row["evidence_id"]))
    return normalized, usable, issues


def _platform(raw: Any) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "platform")
    _keys(row, {"submission_route", "requirements_complete", "fees_known", "account_ready",
                "required_balance", "available_balance", "kyc_status"}, "platform")
    route = _text(row["submission_route"], "platform.submission_route", 32).upper()
    kyc = _text(row["kyc_status"], "platform.kyc_status", 16).upper()
    if route not in _ROUTES or kyc not in _KYC:
        raise ContractQualificationInputError("unsupported platform route or KYC state")
    required, available = _amount(row["required_balance"], "platform.required_balance"), _amount(row["available_balance"], "platform.available_balance")
    complete = _flag(row["requirements_complete"], "platform.requirements_complete")
    fees = _flag(row["fees_known"], "platform.fees_known")
    ready = _flag(row["account_ready"], "platform.account_ready")
    reasons = []
    if not complete: reasons.append("PLATFORM_REQUIREMENTS_INCOMPLETE")
    if not fees: reasons.append("PLATFORM_FEES_UNKNOWN")
    if not ready: reasons.append("PLATFORM_ACCOUNT_NOT_READY")
    if available < required: reasons.append("PLATFORM_BALANCE_INSUFFICIENT")
    if kyc in {"UNKNOWN", "UNSATISFIED"}: reasons.append(f"PLATFORM_KYC_{kyc}")
    return {
        "submission_route": route, "requirements_complete": complete, "fees_known": fees,
        "account_ready": ready, "required_balance": _amount_text(required),
        "available_balance": _amount_text(available), "kyc_status": kyc,
    }, reasons


def _bid(raw: Any, listing: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "bid"); _keys(row, {"currency", "amount", "delivery_days"}, "bid")
    currency, amount = _currency(row["currency"], "bid.currency"), _amount(row["amount"], "bid.amount")
    days = row["delivery_days"]
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ContractQualificationInputError("bid.delivery_days must be a positive integer")
    reasons = []
    if currency != listing["currency"]: reasons.append("BID_CURRENCY_MISMATCH")
    if amount < Decimal(listing["min_amount"]) or amount > Decimal(listing["max_amount"]):
        reasons.append("BID_AMOUNT_OUTSIDE_LISTING_RANGE")
    return {"currency": currency, "amount": _amount_text(amount), "delivery_days": days}, reasons


def qualify_external_contract(snapshot: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    """Evaluate at an explicitly trusted time supplied by a verifier/test harness."""
    root = _obj(snapshot, "snapshot")
    _keys(root, {"schema_version", "listing", "required_claims", "evidence", "platform", "bid"}, "snapshot")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ContractQualificationInputError("snapshot schema_version mismatch")
    canonical_as_of, now = _time(as_of, "as_of")
    listing, reasons, rejected = _listing(root["listing"], now)
    claims, claim_map = _claims(root["required_claims"])
    evidence, usable, evidence_issues = _evidence(root["evidence"], claim_map, now)
    platform, platform_reasons = _platform(root["platform"]); reasons.extend(platform_reasons)
    bid, bid_reasons = _bid(root["bid"], listing); reasons.extend(bid_reasons)

    unproven = sorted(c["claim_id"] for c in claims if c["mandatory"] and not usable[c["claim_id"]])
    for claim_id in unproven:
        reasons.extend(evidence_issues[claim_id])
        reasons.append(f"MANDATORY_CLAIM_UNPROVEN:{claim_id}")
    reasons = sorted(set(reasons))
    disposition = "REJECT" if rejected else ("HOLD" if reasons else "ACTIONABLE")
    status = disposition
    if disposition == "ACTIONABLE":
        status = "READY_FOR_OWNER_SUBMISSION" if platform["submission_route"] == "MANUAL_OWNER" else "READY_FOR_CONNECTED_SUBMISSION"

    projection = {"schema_version": SCHEMA_VERSION, "listing": listing, "required_claims": claims,
                  "evidence": evidence, "platform": platform, "bid": bid, "as_of": canonical_as_of}
    return {
        "schema_version": RECEIPT_VERSION, "disposition": disposition, "status": status,
        "bid_ready": disposition == "ACTIONABLE", "submitted": False,
        "owner_action_required": disposition == "ACTIONABLE" and platform["submission_route"] == "MANUAL_OWNER",
        "canonical_source_url": listing["url"], "source_digest": listing["source_digest"],
        "native_currency": listing["currency"], "bid": bid, "submission_route": platform["submission_route"],
        "unproven_claim_ids": unproven, "reason_codes": reasons,
        "evidence_bindings": [
            {"claim_id": e["claim_id"], "evidence_id": e["evidence_id"], "evidence_type": e["evidence_type"],
             "evidence_digest": e["evidence_digest"], "scope": e["scope"]} for e in evidence
        ],
        "qualification_digest": _sha(projection), "as_of": canonical_as_of,
        "authority": {key: False for key in (
            "marketplace_submission", "terms_acceptance", "account_creation", "kyc_completion",
            "spend", "contract_acceptance", "work_awarded", "payment_received", "revenue_recognized"
        )},
    }


def qualify_external_contract_current(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Production current-time qualification with no caller timestamp input."""
    return qualify_external_contract(snapshot, as_of=trusted_utc_now())


def verify_contract_qualification_receipt(snapshot: dict[str, Any], receipt: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    """Verify integrity, then re-evaluate at an explicitly trusted verifier time."""
    receipt = _obj(receipt, "receipt")
    bound_as_of = receipt.get("as_of")
    if type(bound_as_of) is not str:
        raise ContractQualificationInputError("receipt.as_of is required")
    _, bound_time = _time(bound_as_of, "receipt.as_of")
    current_as_of, current_time = _time(as_of, "as_of")
    if current_time < bound_time:
        raise ContractQualificationInputError("verification time precedes qualification receipt")
    rebuilt = qualify_external_contract(snapshot, as_of=bound_as_of)
    if _canonical(receipt) != _canonical(rebuilt):
        raise ContractQualificationInputError("qualification receipt mismatch")
    current = qualify_external_contract(snapshot, as_of=current_as_of)
    if receipt.get("disposition") == "ACTIONABLE" and current["disposition"] != "ACTIONABLE":
        raise ContractQualificationInputError("qualification receipt is no longer current-actionable")
    return {"valid": True, "qualification_digest": rebuilt["qualification_digest"],
            "status": rebuilt["status"], "current_disposition": current["disposition"],
            "verified_as_of": current_as_of, "submitted": False}


def verify_contract_qualification_receipt_current(
    snapshot: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    """Production receipt verification with verifier-owned current UTC."""
    return verify_contract_qualification_receipt(snapshot, receipt, as_of=trusted_utc_now())


def format_summary(result: dict[str, Any]) -> str:
    return (f"disposition={result['disposition']} status={result['status']} "
            f"bid_ready={str(result['bid_ready']).lower()} submitted=false "
            f"source={result['canonical_source_url']} reasons={','.join(result['reason_codes']) or 'none'}")


def _load(path: str) -> dict[str, Any]:
    if path == "-":
        import sys
        value = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    return _obj(value, "snapshot")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.contract_qualification")
    parser.add_argument("snapshot"); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = qualify_external_contract_current(_load(args.snapshot))
    except (OSError, json.JSONDecodeError, ContractQualificationInputError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else format_summary(result))
    return 0 if result["disposition"] == "ACTIONABLE" else (2 if result["disposition"] == "HOLD" else 3)


if __name__ == "__main__":
    raise SystemExit(main())