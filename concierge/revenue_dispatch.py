"""Compose live revenue intake, paid-work economics, and maintainer availability.

This module is the read-only authorization seam before a paid-work candidate is
allowed into new implementation work.  A candidate must first survive canonical
live intake.  It must then present a paid-work effort/value request and receipt
that verify, recompile exactly, bind to this exact candidate/source, and resolve
to ``GO``.  Only then do we spend another provider read on maintainer
availability.

The binding is anti-replay/integrity protection inside the control plane, not a
cryptographic sponsor signature.  Provider authenticity remains owned by the
canonical intake and the evidence adapters that construct the paid-work request.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .bounty_availability import inspect_bounty_availability
from .paid_work_effort_value_gate import (
    PaidWorkGateInputError,
    compile_paid_work_effort_value_gate,
    verify_receipt as verify_paid_work_gate_receipt,
)
from .revenue_intake import qualify_live_revenue_intake


_SCHEMA = "revenue-dispatch/v2"
_GATE_SCHEMA = "paid-work-effort-value-receipt/v1"
_GATE_DECISIONS = frozenset({"GO", "HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE", "SKIP_ECONOMICS"})
_MAX_GATE_FILE_BYTES = 4 * 1024 * 1024


class RevenueDispatchError(ValueError):
    """Malformed or contradictory dispatch evidence."""


def _require_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise RevenueDispatchError(f"{field} must be a boolean")
    return value


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RevenueDispatchError(f"{field} must be a non-empty stripped string")
    return value


def _require_text_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise RevenueDispatchError(f"{field} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_require_text(item, field=f"{field}[{index}]"))
    return result


def _not_checked_availability(repo: str, number: int, reason: str) -> dict[str, Any]:
    return {
        "schema": "bounty-availability/v1",
        "repo": repo,
        "number": number,
        "disposition": "NOT_CHECKED",
        "dispatch": False,
        "reason_code": reason,
        "issue_state": None,
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


def _gate_block(reason: str, *, decision: str = "INVALID") -> dict[str, Any]:
    return {
        "schema": _GATE_SCHEMA,
        "verified": False,
        "decision": decision,
        "reason_codes": [reason],
        "observed_hold_reasons": [],
        "work_id": None,
        "canonical_source_url": None,
        "canonical_source_identity": None,
        "request_sha256": None,
        "policy_sha256": None,
        "receipt_sha256": None,
        "authority": {
            "effect": "new_work_dispatch_only",
            "receipt_integrity_verified": False,
            "request_recompiled_exactly": False,
            "candidate_identity_bound": False,
            "canonical_source_bound": False,
            "provider_authentication_claimed": False,
        },
    }


def _paid_work_gate_binding(
    repo: str,
    number: int,
    canonical_source_url: str,
    request: Any,
    receipt: Any,
) -> dict[str, Any]:
    if request is None or receipt is None:
        return _gate_block("GATE_EVIDENCE_REQUIRED", decision="NOT_CHECKED")
    if not isinstance(request, dict) or not isinstance(receipt, dict):
        return _gate_block("GATE_EVIDENCE_MALFORMED")

    try:
        verified = verify_paid_work_gate_receipt(receipt)
    except (PaidWorkGateInputError, ValueError, TypeError):
        return _gate_block("RECEIPT_VERIFICATION_FAILED")
    if verified is not True:
        return _gate_block("RECEIPT_VERIFICATION_FAILED")

    try:
        recomputed = compile_paid_work_effort_value_gate(request)
    except (PaidWorkGateInputError, ValueError, TypeError):
        return _gate_block("REQUEST_RECOMPILE_FAILED")
    if recomputed != receipt:
        return _gate_block("RECEIPT_REQUEST_MISMATCH")

    decision = receipt.get("decision")
    if decision not in _GATE_DECISIONS:
        return _gate_block("UNSUPPORTED_GATE_DECISION")

    work_id = receipt.get("work_id")
    receipt_source = receipt.get("canonical_source_url")
    request_candidate = request.get("candidate")
    if not isinstance(request_candidate, dict):
        return _gate_block("REQUEST_CANDIDATE_MALFORMED", decision=decision)
    request_work_id = request_candidate.get("work_id")
    request_source = request_candidate.get("canonical_source_url")
    expected_work_id = f"{repo}#{number}"

    if work_id != expected_work_id or request_work_id != expected_work_id:
        return _gate_block("CANDIDATE_ID_MISMATCH", decision=decision)
    if receipt_source != canonical_source_url or request_source != canonical_source_url:
        return _gate_block("CANONICAL_SOURCE_MISMATCH", decision=decision)

    try:
        reason_codes = _require_text_list(receipt.get("reason_codes"), field="paid_work_gate.reason_codes")
        observed_holds = _require_text_list(
            receipt.get("observed_hold_reasons"),
            field="paid_work_gate.observed_hold_reasons",
        )
        source_identity = _require_text(
            receipt.get("canonical_source_identity"),
            field="paid_work_gate.canonical_source_identity",
        )
        request_sha = _require_text(receipt.get("request_sha256"), field="paid_work_gate.request_sha256")
        policy_sha = _require_text(receipt.get("policy_sha256"), field="paid_work_gate.policy_sha256")
        receipt_sha = _require_text(receipt.get("receipt_sha256"), field="paid_work_gate.receipt_sha256")
    except RevenueDispatchError:
        return _gate_block("RECEIPT_IDENTITY_MALFORMED", decision=decision)

    if not reason_codes:
        return _gate_block("RECEIPT_REASON_CODES_EMPTY", decision=decision)

    return {
        "schema": _GATE_SCHEMA,
        "verified": True,
        "decision": decision,
        "reason_codes": reason_codes,
        "observed_hold_reasons": observed_holds,
        "work_id": work_id,
        "canonical_source_url": receipt_source,
        "canonical_source_identity": source_identity,
        "request_sha256": request_sha,
        "policy_sha256": policy_sha,
        "receipt_sha256": receipt_sha,
        "authority": {
            "effect": "new_work_dispatch_only",
            "receipt_integrity_verified": True,
            "request_recompiled_exactly": True,
            "candidate_identity_bound": True,
            "canonical_source_bound": True,
            "provider_authentication_claimed": False,
        },
    }


def _paid_work_hold(gate: dict[str, Any]) -> tuple[str | None, list[str]]:
    if gate.get("verified") is not True:
        reasons = gate.get("reason_codes")
        code = "INVALID_GATE_EVIDENCE"
        if isinstance(reasons, list) and reasons and isinstance(reasons[0], str):
            code = reasons[0]
        return "HOLD", [f"PAID_WORK_GATE:{code}"]

    decision = gate.get("decision")
    codes = [f"PAID_WORK_GATE:{decision}"]
    for code in gate.get("reason_codes", []):
        if code != "ALL_GATES_CLEAR":
            codes.append(f"PAID_WORK_GATE:{code}")
    for code in gate.get("observed_hold_reasons", []):
        codes.append(f"PAID_WORK_GATE:OBSERVED:{code}")

    if decision == "GO":
        return None, []
    if decision == "SKIP_ECONOMICS":
        return "REJECT", codes
    if decision in {"HOLD_VALUE_UNKNOWN", "HOLD_ACCOUNT_GATE"}:
        return "HOLD", codes
    return "HOLD", ["PAID_WORK_GATE:UNSUPPORTED_GATE_DECISION"]


def _availability_hold(
    intake: dict[str, Any],
    availability: dict[str, Any],
    paid_work_gate: dict[str, Any],
) -> tuple[str, bool, list[str]]:
    intake_dispatch = _require_bool(intake.get("dispatch"), field="intake.dispatch")
    availability_dispatch = _require_bool(
        availability.get("dispatch"), field="availability.dispatch"
    )

    disposition = _require_text(intake.get("disposition"), field="intake.disposition")
    reasons = list(intake.get("reason_codes") or [])
    if not all(isinstance(item, str) and item for item in reasons):
        raise RevenueDispatchError("intake.reason_codes must contain strings")

    gate_disposition, gate_reasons = _paid_work_hold(paid_work_gate)
    if gate_disposition is not None:
        final = "REJECT" if disposition == "REJECT" or gate_disposition == "REJECT" else "HOLD"
        return final, False, reasons + gate_reasons

    if not intake_dispatch:
        return disposition, False, reasons
    if availability_dispatch:
        return disposition, True, reasons

    availability_reason = availability.get("reason_code")
    if not isinstance(availability_reason, str) or not availability_reason:
        raise RevenueDispatchError(
            "non-dispatchable availability receipt requires reason_code"
        )
    final_disposition = "REJECT" if disposition == "REJECT" else "HOLD"
    return final_disposition, False, reasons + [f"AVAILABILITY:{availability_reason}"]


def qualify_available_live_revenue_intake(
    repo: str,
    number: int,
    *,
    listing_url: str | None = None,
    token: str | None = None,
    session=None,
    max_pages: int = 20,
    saturation_threshold: int = 4,
    paid_work_gate_request: dict[str, Any] | None = None,
    paid_work_gate_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorize new paid-work implementation only when all read-only gates clear."""
    intake = qualify_live_revenue_intake(
        repo,
        number,
        listing_url=listing_url,
        token=token,
        session=session,
        max_pages=max_pages,
        saturation_threshold=saturation_threshold,
    )

    intake_dispatch = _require_bool(intake.get("dispatch"), field="intake.dispatch")
    if not intake_dispatch:
        paid_work_gate = _gate_block(
            "UPSTREAM_INTAKE_NOT_DISPATCHABLE",
            decision="NOT_CHECKED",
        )
        availability = _not_checked_availability(
            repo,
            number,
            "UPSTREAM_INTAKE_NOT_DISPATCHABLE",
        )
        disposition, dispatch, reason_codes = _availability_hold(
            intake, availability, paid_work_gate
        )
        return {
            "schema": _SCHEMA,
            "repo": repo,
            "number": number,
            "canonical_source_url": intake.get("canonical_source_url"),
            "disposition": disposition,
            "dispatch": dispatch,
            "reason_codes": reason_codes,
            "reasons": list(intake.get("reasons") or []),
            "qualification": intake.get("qualification"),
            "provenance": intake.get("provenance"),
            "paid_work_gate": paid_work_gate,
            "availability": availability,
            "dispatch_authority": {
                "intake": "canonical_live_intake",
                "paid_work_gate": "not_checked_upstream_hold",
                "availability": "not_checked_upstream_hold",
                "new_work_dispatch": False,
            },
        }

    canonical_source_url = _require_text(
        intake.get("canonical_source_url"),
        field="intake.canonical_source_url",
    )
    paid_work_gate = _paid_work_gate_binding(
        repo,
        number,
        canonical_source_url,
        paid_work_gate_request,
        paid_work_gate_receipt,
    )
    gate_disposition, _ = _paid_work_hold(paid_work_gate)
    if gate_disposition is not None:
        availability = _not_checked_availability(
            repo,
            number,
            "PAID_WORK_GATE_NOT_GO",
        )
        disposition, dispatch, reason_codes = _availability_hold(
            intake, availability, paid_work_gate
        )
        return {
            "schema": _SCHEMA,
            "repo": repo,
            "number": number,
            "canonical_source_url": canonical_source_url,
            "disposition": disposition,
            "dispatch": dispatch,
            "reason_codes": reason_codes,
            "reasons": list(intake.get("reasons") or []),
            "qualification": intake.get("qualification"),
            "provenance": intake.get("provenance"),
            "paid_work_gate": paid_work_gate,
            "availability": availability,
            "dispatch_authority": {
                "intake": "canonical_live_intake",
                "paid_work_gate": "verified_exact_receipt_binding"
                if paid_work_gate.get("verified") is True
                else "fail_closed_evidence_guard",
                "availability": "not_checked_paid_work_gate_hold",
                "new_work_dispatch": False,
            },
        }

    availability = inspect_bounty_availability(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    disposition, dispatch, reason_codes = _availability_hold(
        intake, availability, paid_work_gate
    )
    return {
        "schema": _SCHEMA,
        "repo": repo,
        "number": number,
        "canonical_source_url": canonical_source_url,
        "disposition": disposition,
        "dispatch": dispatch,
        "reason_codes": reason_codes,
        "reasons": list(intake.get("reasons") or []),
        "qualification": intake.get("qualification"),
        "provenance": intake.get("provenance"),
        "paid_work_gate": paid_work_gate,
        "availability": availability,
        "dispatch_authority": {
            "intake": "canonical_live_intake",
            "paid_work_gate": "verified_exact_receipt_binding",
            "availability": "stable_maintainer_outcome_guard",
            "new_work_dispatch": dispatch,
        },
    }


def format_summary(receipt: dict[str, Any]) -> str:
    status = "DISPATCH" if receipt.get("dispatch") is True else receipt.get("disposition")
    gate = receipt.get("paid_work_gate") or {}
    parts = [
        f"{receipt.get('repo')}#{receipt.get('number')}",
        str(status),
        f"economics={gate.get('decision', 'UNKNOWN')}",
    ]
    reasons = receipt.get("reason_codes") or []
    if reasons:
        parts.append(",".join(str(item) for item in reasons))
    return " | ".join(parts)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RevenueDispatchError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise RevenueDispatchError(f"non-finite JSON constant is not allowed: {value}")


def _load_json_object(path: str, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.stat().st_size > _MAX_GATE_FILE_BYTES:
            raise RevenueDispatchError(f"{label} exceeds size bound")
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RevenueDispatchError(f"cannot read {label}: {path}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise RevenueDispatchError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RevenueDispatchError(f"{label} must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Qualify live paid work and compose economics + maintainer availability gates."
    )
    parser.add_argument("repo", help="GitHub repository in owner/name form")
    parser.add_argument("issue", type=int, help="Issue number")
    parser.add_argument("--listing-url")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument(
        "--paid-work-gate-request",
        required=True,
        help="Retained paid-work effort/value request JSON",
    )
    parser.add_argument(
        "--paid-work-gate-receipt",
        required=True,
        help="Compiled paid-work effort/value receipt JSON",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        gate_request = _load_json_object(
            args.paid_work_gate_request,
            label="paid-work gate request",
        )
        gate_receipt = _load_json_object(
            args.paid_work_gate_receipt,
            label="paid-work gate receipt",
        )
        receipt = qualify_available_live_revenue_intake(
            args.repo,
            args.issue,
            listing_url=args.listing_url,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
            paid_work_gate_request=gate_request,
            paid_work_gate_receipt=gate_receipt,
        )
    except RevenueDispatchError as exc:
        raise SystemExit(f"revenue-dispatch: {exc}") from exc

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(format_summary(receipt))
    if receipt.get("dispatch") is True:
        return 0
    if receipt.get("disposition") == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
