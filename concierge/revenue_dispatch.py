# SPDX-License-Identifier: MIT
"""Authoritative paid-work implementation dispatch with value-gate custody.

``revenue_intake.qualify_live_revenue_intake`` proves canonical reward,
provenance, assignment/competition, and other paid-work gates. Maintainer
availability proves the work is still open. A fresh, self-verifying
``paid_work_effort_value_gate`` receipt is then required before this module can
emit internal implementation authority.

This wrapper never grants external claim/submission, payout, cash, or revenue
authority. Missing, stale, held, skipped, mismatched, or tampered economic
receipts fail closed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

import requests

from concierge.bounty_availability import (
    BountyAvailabilityError,
    inspect_bounty_availability,
)
from concierge.paid_work_effort_value_gate import verify_receipt as verify_paid_work_receipt
from concierge.revenue_intake import (
    RevenueIntakeInputError,
    qualify_live_revenue_intake,
)


_GATE_MAX_BYTES = 1024 * 1024
_DEFAULT_GATE_MAX_AGE_SECONDS = 3600
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RevenueDispatchError(RuntimeError):
    """Raised when dispatch evidence cannot be composed safely."""


def _trusted_now() -> datetime:
    return datetime.now(timezone.utc)


def _strict_gate_receipt_bytes(payload: bytes, source: str) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > _GATE_MAX_BYTES:
        raise RevenueDispatchError(f"{source}: invalid receipt byte length")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise RevenueDispatchError(f"{source}: receipt is not UTF-8") from exc

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RevenueDispatchError(
                    f"{source}: duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except RevenueDispatchError:
        raise
    except json.JSONDecodeError as exc:
        raise RevenueDispatchError(f"{source}: receipt is not valid JSON") from exc
    if type(value) is not dict:
        raise RevenueDispatchError(f"{source}: receipt must be a JSON object")
    return value


def _load_gate_receipt(path: str) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.stat().st_size > _GATE_MAX_BYTES:
            raise RevenueDispatchError(f"{path}: receipt is too large")
        return _strict_gate_receipt_bytes(source.read_bytes(), path)
    except RevenueDispatchError:
        raise
    except OSError as exc:
        raise RevenueDispatchError(f"{path}: cannot read receipt") from exc


def _receipt_as_of(value: Any) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise RevenueDispatchError("economic gate receipt as_of is not exact UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise RevenueDispatchError("economic gate receipt as_of is invalid") from exc


def _validate_gate_max_age(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400:
        raise RevenueDispatchError("gate_max_age_seconds must be an integer in 1..86400")
    return value


def _append_hold(
    result: dict[str, Any],
    *,
    code: str,
    message: str,
    economics: dict[str, Any],
) -> dict[str, Any]:
    existing_codes = result.get("reason_codes")
    if not isinstance(existing_codes, list) or not all(
        isinstance(item, str) for item in existing_codes
    ):
        raise RevenueDispatchError("intake reason_codes must be a list of strings")
    codes = list(existing_codes)
    if code not in codes:
        codes.append(code)

    existing_reasons = result.get("reasons")
    if not isinstance(existing_reasons, list):
        raise RevenueDispatchError("intake reasons must be a list")
    reasons = list(existing_reasons)
    reasons.append(
        {
            "gate": "paid_work_effort_value",
            "code": code.split(":", 1)[-1],
            "severity": "HOLD",
            "message": message,
        }
    )

    held = dict(result)
    held["reason_codes"] = codes
    held["reasons"] = reasons
    held["dispatch"] = False
    if held.get("disposition") != "REJECT":
        held["disposition"] = "HOLD"
    held["economic_admission"] = economics
    authority = dict(held.get("dispatch_authority") or {})
    authority.update(
        {
            "economics": "paid_work_effort_value_gate",
            "new_work_dispatch": False,
            "internal_implementation_only": False,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
        }
    )
    held["dispatch_authority"] = authority
    return held


def _apply_paid_work_gate(
    result: dict[str, Any],
    gate_receipt: dict[str, Any] | None,
    *,
    work_id: str | None,
    gate_max_age_seconds: int,
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("dispatch") is not True:
        raise RevenueDispatchError("economic gate may only promote a clear dispatch candidate")
    max_age = _validate_gate_max_age(gate_max_age_seconds)

    if gate_receipt is None:
        return _append_hold(
            result,
            code="ECONOMICS:GATE_RECEIPT_REQUIRED",
            message=(
                "A verified paid-work effort/value receipt is required before "
                "internal implementation can be dispatched."
            ),
            economics={
                "status": "MISSING",
                "decision": None,
                "receipt_sha256": None,
                "verified": False,
            },
        )
    if type(gate_receipt) is not dict:
        raise RevenueDispatchError("economic gate receipt must be an object")
    if not isinstance(work_id, str) or not work_id or work_id != work_id.strip():
        raise RevenueDispatchError("work_id is required to bind economic admission")
    if not verify_paid_work_receipt(gate_receipt):
        raise RevenueDispatchError("economic gate receipt failed integrity verification")

    receipt_work_id = gate_receipt.get("work_id")
    if receipt_work_id != work_id:
        raise RevenueDispatchError("economic gate receipt work_id mismatch")
    canonical_source_url = result.get("canonical_source_url")
    if not isinstance(canonical_source_url, str) or not canonical_source_url:
        raise RevenueDispatchError("dispatch candidate is missing canonical_source_url")
    if gate_receipt.get("canonical_source_url") != canonical_source_url:
        raise RevenueDispatchError("economic gate canonical source mismatch")

    as_of = _receipt_as_of(gate_receipt.get("as_of"))
    now = _trusted_now()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise RevenueDispatchError("trusted clock did not return timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    age_seconds = int((now - as_of).total_seconds())
    if age_seconds < 0:
        raise RevenueDispatchError("economic gate receipt is from the future")
    if age_seconds > max_age:
        raise RevenueDispatchError("economic gate receipt is stale")

    decision = gate_receipt.get("decision")
    if decision not in {
        "GO",
        "HOLD_VALUE_UNKNOWN",
        "HOLD_ACCOUNT_GATE",
        "SKIP_ECONOMICS",
    }:
        raise RevenueDispatchError("economic gate receipt decision is unsupported")
    digest = gate_receipt.get("receipt_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RevenueDispatchError("economic gate receipt digest is malformed")

    gate_authority = gate_receipt.get("authority")
    if type(gate_authority) is not dict:
        raise RevenueDispatchError("economic gate receipt authority is missing")
    expected_authority = {
        "go_is_internal_admission_signal": True,
        "external_claim_authority": False,
        "external_submission_authority": False,
        "payment_cash_or_revenue_authority": False,
        "fx_conversion": False,
        "noncash_valuation": False,
    }
    for key, expected in expected_authority.items():
        if gate_authority.get(key) is not expected:
            raise RevenueDispatchError(
                f"economic gate authority field {key!r} is invalid"
            )

    economics = {
        "status": "VERIFIED",
        "decision": decision,
        "receipt_sha256": digest,
        "work_id": work_id,
        "canonical_source_url": canonical_source_url,
        "as_of": gate_receipt["as_of"],
        "age_seconds": age_seconds,
        "max_age_seconds": max_age,
        "verified": True,
    }
    if decision != "GO":
        return _append_hold(
            result,
            code=f"ECONOMICS:{decision}",
            message=(
                f"Paid-work economics gate returned {decision}; internal "
                "implementation dispatch is not authorized."
            ),
            economics=economics,
        )

    promoted = dict(result)
    promoted["economic_admission"] = economics
    promoted["dispatch"] = True
    authority = dict(promoted.get("dispatch_authority") or {})
    authority.update(
        {
            "economics": "verified_paid_work_effort_value_gate",
            "new_work_dispatch": True,
            "internal_implementation_only": True,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
        }
    )
    promoted["dispatch_authority"] = authority
    return promoted


def _availability_hold(
    intake: dict[str, Any],
    availability: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(intake, dict) or not isinstance(availability, dict):
        raise RevenueDispatchError("dispatch gate result must be an object")
    if type(intake.get("dispatch")) is not bool:
        raise RevenueDispatchError("intake dispatch must be boolean")
    if type(availability.get("dispatch")) is not bool:
        raise RevenueDispatchError("availability dispatch must be boolean")

    result = dict(intake)
    result["availability"] = availability
    if availability["dispatch"]:
        result["dispatch_authority"] = {
            "intake": "canonical_live_intake",
            "availability": "stable_maintainer_outcome_guard",
            "economics": "required_before_implementation",
            "new_work_dispatch": False,
            "internal_implementation_only": False,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
        }
        return result

    reason = availability.get("reason_code")
    if not isinstance(reason, str) or not reason:
        raise RevenueDispatchError(
            "non-dispatchable availability result was missing reason_code"
        )
    existing_codes = result.get("reason_codes")
    if not isinstance(existing_codes, list) or not all(
        isinstance(code, str) for code in existing_codes
    ):
        raise RevenueDispatchError("intake reason_codes must be a list of strings")
    code = f"AVAILABILITY:{reason}"
    codes = list(existing_codes)
    if code not in codes:
        codes.append(code)

    existing_reasons = result.get("reasons")
    if not isinstance(existing_reasons, list):
        raise RevenueDispatchError("intake reasons must be a list")
    reasons = list(existing_reasons)
    reasons.append(
        {
            "gate": "availability",
            "code": reason,
            "severity": "HOLD",
            "message": (
                "Canonical maintainer availability evidence requires human review "
                "before any new paid work is dispatched."
            ),
        }
    )

    result["reason_codes"] = codes
    result["reasons"] = reasons
    result["dispatch"] = False
    if result.get("disposition") != "REJECT":
        result["disposition"] = "HOLD"
    result["dispatch_authority"] = {
        "intake": "canonical_live_intake",
        "availability": "stable_maintainer_outcome_guard",
        "economics": "not_reached",
        "new_work_dispatch": False,
        "internal_implementation_only": False,
        "external_claim_authority": False,
        "external_submission_authority": False,
        "payment_cash_or_revenue_authority": False,
    }
    return result


def qualify_available_live_revenue_intake(
    repo: str,
    number: int,
    *,
    listing_url: str | None = None,
    token: str | None = None,
    session: Any = requests,
    max_pages: int = 10,
    saturation_threshold: int = 4,
    gate_receipt: dict[str, Any] | None = None,
    work_id: str | None = None,
    gate_max_age_seconds: int = _DEFAULT_GATE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Authorize internal implementation only when live + economic gates clear."""
    intake = qualify_live_revenue_intake(
        repo,
        number,
        listing_url=listing_url,
        token=token,
        session=session,
        max_pages=max_pages,
        saturation_threshold=saturation_threshold,
    )
    if not isinstance(intake, dict):
        raise RevenueDispatchError("live revenue intake did not return an object")
    intake_dispatch = intake.get("dispatch")
    if type(intake_dispatch) is not bool:
        raise RevenueDispatchError("live revenue intake dispatch must be boolean")

    if not intake_dispatch:
        result = dict(intake)
        result["availability"] = {
            "schema": "bounty-availability/v1",
            "repo": repo,
            "number": number,
            "disposition": "NOT_CHECKED",
            "dispatch": False,
            "reason_code": "UPSTREAM_INTAKE_NOT_DISPATCHABLE",
            "signal_codes": [],
            "evidence": [],
            "authority": {
                "effect": "new_work_dispatch_only",
                "terminal_signal_is_payout_proof": False,
                "terminal_signal_is_revenue_proof": False,
                "raw_comment_text_retained": False,
                "user_identity_retained": False,
            },
        }
        result["economic_admission"] = {
            "status": "NOT_CHECKED",
            "decision": None,
            "receipt_sha256": None,
            "verified": False,
        }
        result["dispatch_authority"] = {
            "intake": "canonical_live_intake",
            "availability": "not_needed",
            "economics": "not_needed",
            "new_work_dispatch": False,
            "internal_implementation_only": False,
            "external_claim_authority": False,
            "external_submission_authority": False,
            "payment_cash_or_revenue_authority": False,
        }
        return result

    availability = inspect_bounty_availability(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    result = _availability_hold(intake, availability)
    if not result["dispatch"]:
        result["economic_admission"] = {
            "status": "NOT_CHECKED",
            "decision": None,
            "receipt_sha256": None,
            "verified": False,
        }
        return result
    return _apply_paid_work_gate(
        result,
        gate_receipt,
        work_id=work_id,
        gate_max_age_seconds=gate_max_age_seconds,
    )


def format_summary(result: dict[str, Any]) -> str:
    availability = result.get("availability")
    availability_reason = (
        availability.get("reason_code")
        if isinstance(availability, dict)
        else "missing"
    )
    economics = result.get("economic_admission")
    economic_decision = (
        economics.get("decision") if isinstance(economics, dict) else None
    )
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"source={result.get('canonical_source_url') or 'none'} "
        f"availability_reason={availability_reason or 'none'} "
        f"economic_decision={economic_decision or 'none'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_dispatch",
        description=(
            "Authorize internal paid-work implementation only after live intake, "
            "maintainer availability, and verified effort/value admission."
        ),
    )
    parser.add_argument("repo", help="canonical GitHub repository in owner/name form")
    parser.add_argument("issue", type=int, help="canonical bounty issue number")
    parser.add_argument("--listing-url")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--gate-receipt", required=True, help="verified paid-work gate receipt JSON")
    parser.add_argument("--work-id", required=True, help="exact work identity bound by the gate receipt")
    parser.add_argument(
        "--gate-max-age-seconds",
        type=int,
        default=_DEFAULT_GATE_MAX_AGE_SECONDS,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        gate_receipt = _load_gate_receipt(args.gate_receipt)
        result = qualify_available_live_revenue_intake(
            args.repo,
            args.issue,
            listing_url=args.listing_url,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            gate_receipt=gate_receipt,
            work_id=args.work_id,
            gate_max_age_seconds=args.gate_max_age_seconds,
        )
    except (
        BountyAvailabilityError,
        RevenueDispatchError,
        RevenueIntakeInputError,
        ValueError,
    ) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    if result["dispatch"]:
        return 0
    if result.get("disposition") == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
