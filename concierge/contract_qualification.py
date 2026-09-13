# SPDX-License-Identifier: MIT
"""Fail-closed authority for external paid-contract qualification.

Current submission readiness is derived only from two independently acquired,
content-addressed authority records: a complete screening-requirements capture
and a fresh platform-state capture. The request itself carries only evidence
and the proposed bid. Production qualification samples process UTC; historical
replay is separate and never emits current submission authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "bounty-concierge.external-contract-qualification/v2"
RECEIPT_VERSION = "bounty-concierge.external-contract-qualification-receipt/v2"
REQUIREMENTS_AUTHORITY_SCHEMA = "bounty-concierge.external-contract-requirements-authority/v1"
PLATFORM_AUTHORITY_SCHEMA = "bounty-concierge.external-contract-platform-authority/v1"
FORENSIC_SCHEMA = "bounty-concierge.external-contract-forensic-replay/v1"
_REQUIREMENTS_ENV = "CONTRACT_REQUIREMENTS_GENERATION"
_PLATFORM_ENV = "CONTRACT_PLATFORM_GENERATION"
_LISTING_TTL = 30 * 60
_REQUIREMENTS_TTL = 30 * 60
_PLATFORM_TTL = 5 * 60
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
_PLATFORM_SOURCES = frozenset({"PROVIDER_READBACK", "OWNER_ATTESTATION"})


class ContractQualificationInputError(ValueError):
    """Input or authority material is not structurally trustworthy."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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


def _identifier(value: Any, name: str, limit: int = 96) -> str:
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


def _utc_now() -> str:
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
    if not parsed.is_finite() or parsed < 0 or parsed.as_tuple().exponent < -2 or parsed.adjusted() > 18:
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


def _claims(raw: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if type(raw) is not list:
        raise ContractQualificationInputError("required_claims must be a list")
    if len(raw) > 100:
        raise ContractQualificationInputError("required_claims exceeds supported boundary")
    rows: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        row = _obj(item, f"required_claims[{index}]")
        _keys(row, {"claim_id", "claim_type", "mandatory"}, f"required_claims[{index}]")
        claim_id = _identifier(row["claim_id"], f"required_claims[{index}].claim_id", 80)
        if claim_id in by_id:
            raise ContractQualificationInputError(f"duplicate claim_id: {claim_id}")
        claim_type = _text(row["claim_type"], f"required_claims[{index}].claim_type", 24).upper()
        if claim_type not in _CLAIM_TYPES:
            raise ContractQualificationInputError(f"unsupported claim_type: {claim_type}")
        normalized = {
            "claim_id": claim_id,
            "claim_type": claim_type,
            "mandatory": _flag(row["mandatory"], f"required_claims[{index}].mandatory"),
        }
        rows.append(normalized)
        by_id[claim_id] = normalized
    rows.sort(key=lambda row: row["claim_id"])
    return rows, by_id


def _listing(raw: Any, now: int) -> tuple[dict[str, Any], list[str], bool]:
    row = _obj(raw, "listing")
    _keys(row, {"url", "state", "observed_at", "closes_at", "source_digest", "currency", "min_amount", "max_amount"}, "listing")
    state = _text(row["state"], "listing.state", 16).upper()
    if state not in _STATES:
        raise ContractQualificationInputError("listing.state is unknown")
    observed_at, observed = _time(row["observed_at"], "listing.observed_at")
    if observed > now:
        raise ContractQualificationInputError("listing observation is from the future")
    closes_at, closes = None, None
    if row["closes_at"] is not None:
        closes_at, closes = _time(row["closes_at"], "listing.closes_at")
    minimum = _amount(row["min_amount"], "listing.min_amount")
    maximum = _amount(row["max_amount"], "listing.max_amount")
    if minimum > maximum:
        raise ContractQualificationInputError("listing amount range is inverted")
    normalized = {
        "url": _url(row["url"], "listing.url"),
        "state": state,
        "observed_at": observed_at,
        "closes_at": closes_at,
        "source_digest": _digest(row["source_digest"], "listing.source_digest"),
        "currency": _currency(row["currency"], "listing.currency"),
        "min_amount": _amount_text(minimum),
        "max_amount": _amount_text(maximum),
    }
    reasons: list[str] = []
    rejected = False
    if state == "CLOSED":
        reasons.append("LISTING_CLOSED")
        rejected = True
    elif state == "UNKNOWN":
        reasons.append("LISTING_STATE_UNKNOWN")
    if now - observed > _LISTING_TTL:
        reasons.append("LISTING_OBSERVATION_STALE")
    if closes is not None and closes <= now:
        reasons.append("LISTING_DEADLINE_PASSED")
        rejected = True
    return normalized, reasons, rejected


def _normalize_requirements_authority(raw: Any, expected_generation: str, now: int):
    row = _obj(raw, "requirements_authority")
    _keys(row, {"schema_version", "authority_id", "extractor_id", "captured_at", "complete", "listing", "required_claims", "generation"}, "requirements_authority")
    if row["schema_version"] != REQUIREMENTS_AUTHORITY_SCHEMA:
        raise ContractQualificationInputError("requirements authority schema mismatch")
    authority_id = _identifier(row["authority_id"], "requirements_authority.authority_id")
    extractor_id = _identifier(row["extractor_id"], "requirements_authority.extractor_id")
    captured_at, captured = _time(row["captured_at"], "requirements_authority.captured_at")
    if captured > now:
        raise ContractQualificationInputError("requirements authority is from the future")
    complete = _flag(row["complete"], "requirements_authority.complete")
    listing, listing_reasons, rejected = _listing(row["listing"], now)
    claims, claim_map = _claims(row["required_claims"])
    body = {
        "schema_version": REQUIREMENTS_AUTHORITY_SCHEMA,
        "authority_id": authority_id,
        "extractor_id": extractor_id,
        "captured_at": captured_at,
        "complete": complete,
        "listing": listing,
        "required_claims": claims,
    }
    generation = _digest(row["generation"], "requirements_authority.generation")
    expected_generation = _digest(expected_generation, "expected_requirements_generation")
    if generation != _sha(body):
        raise ContractQualificationInputError("requirements authority generation mismatch")
    if generation != expected_generation:
        raise ContractQualificationInputError("requirements authority is not the independently expected generation")
    reasons = list(listing_reasons)
    if not complete:
        reasons.append("SCREENING_REQUIREMENTS_INCOMPLETE")
    if now - captured > _REQUIREMENTS_TTL:
        reasons.append("SCREENING_REQUIREMENTS_AUTHORITY_STALE")
    return body | {"generation": generation}, claim_map, sorted(set(reasons)), rejected


def _normalize_platform_authority(raw: Any, expected_generation: str, listing: dict[str, Any], now: int):
    row = _obj(raw, "platform_authority")
    _keys(row, {"schema_version", "authority_id", "source_type", "provider", "account_ref", "captured_at", "complete", "listing_url", "listing_source_digest", "platform", "generation"}, "platform_authority")
    if row["schema_version"] != PLATFORM_AUTHORITY_SCHEMA:
        raise ContractQualificationInputError("platform authority schema mismatch")
    authority_id = _identifier(row["authority_id"], "platform_authority.authority_id")
    source_type = _text(row["source_type"], "platform_authority.source_type", 32).upper()
    if source_type not in _PLATFORM_SOURCES:
        raise ContractQualificationInputError("unsupported platform authority source_type")
    provider = _identifier(row["provider"], "platform_authority.provider")
    account_ref = _identifier(row["account_ref"], "platform_authority.account_ref")
    captured_at, captured = _time(row["captured_at"], "platform_authority.captured_at")
    if captured > now:
        raise ContractQualificationInputError("platform authority is from the future")
    complete = _flag(row["complete"], "platform_authority.complete")
    listing_url = _url(row["listing_url"], "platform_authority.listing_url")
    listing_source_digest = _digest(row["listing_source_digest"], "platform_authority.listing_source_digest")
    if listing_url != listing["url"] or listing_source_digest != listing["source_digest"]:
        raise ContractQualificationInputError("platform authority listing binding mismatch")
    platform_row = _obj(row["platform"], "platform_authority.platform")
    _keys(platform_row, {"submission_route", "requirements_complete", "fees_known", "account_ready", "required_balance", "available_balance", "kyc_status"}, "platform_authority.platform")
    route = _text(platform_row["submission_route"], "platform.submission_route", 32).upper()
    kyc = _text(platform_row["kyc_status"], "platform.kyc_status", 16).upper()
    if route not in _ROUTES or kyc not in _KYC:
        raise ContractQualificationInputError("unsupported platform route or KYC state")
    required = _amount(platform_row["required_balance"], "platform.required_balance")
    available = _amount(platform_row["available_balance"], "platform.available_balance")
    platform = {
        "submission_route": route,
        "requirements_complete": _flag(platform_row["requirements_complete"], "platform.requirements_complete"),
        "fees_known": _flag(platform_row["fees_known"], "platform.fees_known"),
        "account_ready": _flag(platform_row["account_ready"], "platform.account_ready"),
        "required_balance": _amount_text(required),
        "available_balance": _amount_text(available),
        "kyc_status": kyc,
    }
    body = {
        "schema_version": PLATFORM_AUTHORITY_SCHEMA,
        "authority_id": authority_id,
        "source_type": source_type,
        "provider": provider,
        "account_ref": account_ref,
        "captured_at": captured_at,
        "complete": complete,
        "listing_url": listing_url,
        "listing_source_digest": listing_source_digest,
        "platform": platform,
    }
    generation = _digest(row["generation"], "platform_authority.generation")
    expected_generation = _digest(expected_generation, "expected_platform_generation")
    if generation != _sha(body):
        raise ContractQualificationInputError("platform authority generation mismatch")
    if generation != expected_generation:
        raise ContractQualificationInputError("platform authority is not the independently expected generation")
    reasons: list[str] = []
    if not complete:
        reasons.append("PLATFORM_AUTHORITY_INCOMPLETE")
    if now - captured > _PLATFORM_TTL:
        reasons.append("PLATFORM_AUTHORITY_STALE")
    if not platform["requirements_complete"]:
        reasons.append("PLATFORM_REQUIREMENTS_INCOMPLETE")
    if not platform["fees_known"]:
        reasons.append("PLATFORM_FEES_UNKNOWN")
    if not platform["account_ready"]:
        reasons.append("PLATFORM_ACCOUNT_NOT_READY")
    if available < required:
        reasons.append("PLATFORM_BALANCE_INSUFFICIENT")
    if kyc in {"UNKNOWN", "UNSATISFIED"}:
        reasons.append(f"PLATFORM_KYC_{kyc}")
    if route == "CONNECTED_ACTION" and source_type != "PROVIDER_READBACK":
        reasons.append("CONNECTED_ROUTE_REQUIRES_PROVIDER_READBACK")
    return body | {"generation": generation}, sorted(set(reasons))


def _evidence(raw: Any, claims: dict[str, dict[str, Any]], now: int):
    if type(raw) is not list:
        raise ContractQualificationInputError("evidence must be a list")
    if len(raw) > 500:
        raise ContractQualificationInputError("evidence exceeds supported boundary")
    normalized: list[dict[str, Any]] = []
    usable = {claim_id: [] for claim_id in claims}
    issues = {claim_id: set() for claim_id in claims}
    seen: set[str] = set()
    for index, item in enumerate(raw):
        row = _obj(item, f"evidence[{index}]")
        _keys(row, {"evidence_id", "claim_id", "evidence_type", "evidence_ref", "evidence_digest", "observed_at", "scope"}, f"evidence[{index}]")
        evidence_id = _identifier(row["evidence_id"], f"evidence[{index}].evidence_id")
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
        item_normalized = {
            "evidence_id": evidence_id,
            "claim_id": claim_id,
            "evidence_type": kind,
            "evidence_ref": _text(row["evidence_ref"], f"evidence[{index}].evidence_ref", 2048),
            "evidence_digest": _digest(row["evidence_digest"], f"evidence[{index}].evidence_digest"),
            "observed_at": observed_at,
            "scope": scope,
        }
        normalized.append(item_normalized)
        claim = claims[claim_id]
        if scope != "EXACT":
            issues[claim_id].add(f"ADJACENT_EVIDENCE:{claim_id}")
        elif kind not in _ALLOWED[claim["claim_type"]]:
            issues[claim_id].add(f"EVIDENCE_TYPE_MISMATCH:{claim_id}")
        elif claim["claim_type"] == "AVAILABILITY" and now - observed > _AVAILABILITY_TTL:
            issues[claim_id].add(f"AVAILABILITY_EVIDENCE_STALE:{claim_id}")
        else:
            usable[claim_id].append(item_normalized)
    normalized.sort(key=lambda row: (row["claim_id"], row["evidence_id"]))
    return normalized, usable, issues


def _bid(raw: Any, listing: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    row = _obj(raw, "bid")
    _keys(row, {"currency", "amount", "delivery_days"}, "bid")
    currency = _currency(row["currency"], "bid.currency")
    amount = _amount(row["amount"], "bid.amount")
    days = row["delivery_days"]
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ContractQualificationInputError("bid.delivery_days must be a positive integer")
    reasons: list[str] = []
    if currency != listing["currency"]:
        reasons.append("BID_CURRENCY_MISMATCH")
    if amount < Decimal(listing["min_amount"]) or amount > Decimal(listing["max_amount"]):
        reasons.append("BID_AMOUNT_OUTSIDE_LISTING_RANGE")
    return {"currency": currency, "amount": _amount_text(amount), "delivery_days": days}, reasons


def _evaluate_at(
    request: dict[str, Any],
    requirements_authority: dict[str, Any],
    platform_authority: dict[str, Any],
    *,
    expected_requirements_generation: str,
    expected_platform_generation: str,
    as_of: str,
) -> dict[str, Any]:
    root = _obj(request, "request")
    _keys(root, {"schema_version", "evidence", "bid"}, "request")
    if root["schema_version"] != SCHEMA_VERSION:
        raise ContractQualificationInputError("request schema_version mismatch")
    evaluated_at, now = _time(as_of, "as_of")
    requirements, claim_map, reasons, rejected = _normalize_requirements_authority(
        requirements_authority, expected_requirements_generation, now
    )
    platform, platform_reasons = _normalize_platform_authority(
        platform_authority, expected_platform_generation, requirements["listing"], now
    )
    reasons.extend(platform_reasons)
    evidence, usable, evidence_issues = _evidence(root["evidence"], claim_map, now)
    bid, bid_reasons = _bid(root["bid"], requirements["listing"])
    reasons.extend(bid_reasons)
    unproven = sorted(
        claim["claim_id"]
        for claim in requirements["required_claims"]
        if claim["mandatory"] and not usable[claim["claim_id"]]
    )
    for claim_id in unproven:
        reasons.extend(evidence_issues[claim_id])
        reasons.append(f"MANDATORY_CLAIM_UNPROVEN:{claim_id}")
    reasons = sorted(set(reasons))
    disposition = "REJECT" if rejected else ("HOLD" if reasons else "ACTIONABLE")
    status = disposition
    if disposition == "ACTIONABLE":
        status = (
            "READY_FOR_OWNER_SUBMISSION"
            if platform["platform"]["submission_route"] == "MANUAL_OWNER"
            else "READY_FOR_CONNECTED_SUBMISSION"
        )
    projection = {
        "schema_version": SCHEMA_VERSION,
        "requirements_generation": requirements["generation"],
        "platform_generation": platform["generation"],
        "evidence": evidence,
        "bid": bid,
        "evaluated_at": evaluated_at,
    }
    return {
        "schema_version": RECEIPT_VERSION,
        "disposition": disposition,
        "status": status,
        "bid_ready": disposition == "ACTIONABLE",
        "submitted": False,
        "owner_action_required": disposition == "ACTIONABLE" and platform["platform"]["submission_route"] == "MANUAL_OWNER",
        "canonical_source_url": requirements["listing"]["url"],
        "source_digest": requirements["listing"]["source_digest"],
        "native_currency": requirements["listing"]["currency"],
        "bid": bid,
        "submission_route": platform["platform"]["submission_route"],
        "requirements_authority_id": requirements["authority_id"],
        "requirements_generation": requirements["generation"],
        "platform_authority_id": platform["authority_id"],
        "platform_generation": platform["generation"],
        "platform_account_ref": platform["account_ref"],
        "unproven_claim_ids": unproven,
        "reason_codes": reasons,
        "evidence_bindings": [
            {
                "claim_id": item["claim_id"],
                "evidence_id": item["evidence_id"],
                "evidence_type": item["evidence_type"],
                "evidence_digest": item["evidence_digest"],
                "scope": item["scope"],
            }
            for item in evidence
        ],
        "qualification_digest": _sha(projection),
        "evaluated_at": evaluated_at,
        "authority": {key: False for key in (
            "marketplace_submission", "terms_acceptance", "account_creation", "kyc_completion",
            "spend", "contract_acceptance", "work_awarded", "payment_received", "revenue_recognized",
        )},
    }


def qualify_external_contract(
    request: dict[str, Any],
    requirements_authority: dict[str, Any],
    platform_authority: dict[str, Any],
    *,
    expected_requirements_generation: str,
    expected_platform_generation: str,
) -> dict[str, Any]:
    """Evaluate current readiness using process UTC; callers cannot choose the clock."""
    return _evaluate_at(
        request,
        requirements_authority,
        platform_authority,
        expected_requirements_generation=expected_requirements_generation,
        expected_platform_generation=expected_platform_generation,
        as_of=_utc_now(),
    )


def replay_external_contract(
    request: dict[str, Any],
    requirements_authority: dict[str, Any],
    platform_authority: dict[str, Any],
    *,
    expected_requirements_generation: str,
    expected_platform_generation: str,
    as_of: str,
) -> dict[str, Any]:
    """Forensic historical evaluation that never emits current submission authority."""
    historical = _evaluate_at(
        request,
        requirements_authority,
        platform_authority,
        expected_requirements_generation=expected_requirements_generation,
        expected_platform_generation=expected_platform_generation,
        as_of=as_of,
    )
    return {
        "schema_version": FORENSIC_SCHEMA,
        "status": "FORENSIC_REPLAY_ONLY",
        "bid_ready": False,
        "submitted": False,
        "historical_disposition": historical["disposition"],
        "historical_reason_codes": historical["reason_codes"],
        "qualification_digest": historical["qualification_digest"],
        "replayed_at": historical["evaluated_at"],
        "authority": {"marketplace_submission": False, "contract_acceptance": False, "revenue_recognized": False},
    }


def verify_contract_qualification_receipt(
    request: dict[str, Any],
    requirements_authority: dict[str, Any],
    platform_authority: dict[str, Any],
    receipt: dict[str, Any],
    *,
    expected_requirements_generation: str,
    expected_platform_generation: str,
) -> dict[str, Any]:
    receipt = _obj(receipt, "receipt")
    bound_at = receipt.get("evaluated_at")
    if type(bound_at) is not str:
        raise ContractQualificationInputError("receipt.evaluated_at is required")
    rebuilt = _evaluate_at(
        request,
        requirements_authority,
        platform_authority,
        expected_requirements_generation=expected_requirements_generation,
        expected_platform_generation=expected_platform_generation,
        as_of=bound_at,
    )
    if _canonical(receipt) != _canonical(rebuilt):
        raise ContractQualificationInputError("qualification receipt mismatch")
    current = qualify_external_contract(
        request,
        requirements_authority,
        platform_authority,
        expected_requirements_generation=expected_requirements_generation,
        expected_platform_generation=expected_platform_generation,
    )
    if receipt.get("disposition") == "ACTIONABLE" and current["disposition"] != "ACTIONABLE":
        raise ContractQualificationInputError("qualification receipt is no longer current-actionable")
    return {
        "valid": True,
        "qualification_digest": rebuilt["qualification_digest"],
        "status": rebuilt["status"],
        "current_disposition": current["disposition"],
        "verified_at": current["evaluated_at"],
        "submitted": False,
    }


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractQualificationInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: str, name: str) -> dict[str, Any]:
    try:
        if path == "-":
            import sys
            value = json.load(sys.stdin, object_pairs_hook=_strict_object)
        else:
            with Path(path).open("r", encoding="utf-8") as handle:
                value = json.load(handle, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ContractQualificationInputError(f"{name} is not strict JSON") from exc
    return _obj(value, name)


def _expected_generations_from_env() -> tuple[str, str]:
    req = os.environ.get(_REQUIREMENTS_ENV)
    platform = os.environ.get(_PLATFORM_ENV)
    if req is None or platform is None:
        raise ContractQualificationInputError(
            f"trusted integration must set {_REQUIREMENTS_ENV} and {_PLATFORM_ENV}"
        )
    return _digest(req, _REQUIREMENTS_ENV), _digest(platform, _PLATFORM_ENV)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m concierge.contract_qualification")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("qualify", "replay"):
        target = sub.add_parser(command)
        target.add_argument("request")
        target.add_argument("requirements_authority")
        target.add_argument("platform_authority")
        target.add_argument("--json", action="store_true")
        if command == "replay":
            target.add_argument("--as-of", required=True)
    args = parser.parse_args(argv)
    try:
        request = _load(args.request, "request")
        requirements = _load(args.requirements_authority, "requirements_authority")
        platform = _load(args.platform_authority, "platform_authority")
        expected_req, expected_platform = _expected_generations_from_env()
        if args.command == "qualify":
            result = qualify_external_contract(
                request, requirements, platform,
                expected_requirements_generation=expected_req,
                expected_platform_generation=expected_platform,
            )
        else:
            result = replay_external_contract(
                request, requirements, platform,
                expected_requirements_generation=expected_req,
                expected_platform_generation=expected_platform,
                as_of=args.as_of,
            )
    except (OSError, ContractQualificationInputError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else _canonical(result))
    if args.command == "replay":
        return 4
    return 0 if result["disposition"] == "ACTIONABLE" else (2 if result["disposition"] == "HOLD" else 3)


if __name__ == "__main__":
    raise SystemExit(main())
