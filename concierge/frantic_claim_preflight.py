# SPDX-License-Identifier: MIT
"""Live Frantic bounty slot/economics preflight (read-only, fail-closed).

Use this at the *start* of each Frantic candidate dispatch, not as a permanent
queue eligibility receipt. All prices, fees, claim availability and funding
state come from Frantic's public GET /v1/bounties/{id} on every invocation.
No claiming, payout, account setup, source authoring or authorization occurs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

import requests


SCHEMA = "frantic-live-claim-preflight/v1"
BASE_URL = "https://gofrantic.com"
OPENAPI_URL = BASE_URL + "/openapi.json"
MIN_WORKER_PRICE_CENTS = 1500  # Owner's hard $15 minimum on green platforms.
MAX_BOUNTY_ID = 2_147_483_647
_ALLOWED_ACTIONS = frozenset({
    "available", "requires_identity", "configured_closed", "unavailable"
})


class FranticPreflightError(ValueError):
    """Malformed first-party bounty evidence."""


def _bounty_id(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_BOUNTY_ID:
        raise FranticPreflightError("bounty_id must be a positive integer")
    return value


def _cents(value: Any, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise FranticPreflightError(f"{field} must be non-negative integer cents")
    return value


def _amount_matches(cents: int | None, usd: Any, field: str) -> None:
    if cents is None and usd is None:
        return
    if cents is None or type(usd) not in {int, float}:
        raise FranticPreflightError(f"{field} cents/USD fields disagree")
    try:
        amount = Decimal(str(usd))
    except (InvalidOperation, ValueError) as exc:
        raise FranticPreflightError(f"{field} amount malformed") from exc
    if not amount.is_finite() or amount < 0 or amount * 100 != cents:
        raise FranticPreflightError(f"{field} cents/USD fields disagree")


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise FranticPreflightError(f"{field} must be a non-negative integer")
    return value


def _at_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _receipt(bounty_id: int, observed_at: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "bounty_id": bounty_id,
        "source_url": f"{BASE_URL}/v1/bounties/{bounty_id}",
        "contract_url": OPENAPI_URL,
        "observed_at": observed_at,  # HTTP observation time, not provider attestation.
        "price_usd": None,
        "vendor_fee_usd": None,  # This is NOT a deduction from the worker reward.
        "funded": None,
        "capacity": None,
        "occupied_slots": None,
        "available_slots": None,
        "claim_gate_state": None,
        "claim_gate_available": None,
        "posting_status": None,
        "work_status": None,
        "decision": "HOLD_PROVIDER_EVIDENCE_MISSING",
        "reason_codes": [],
        "authority": {
            "internal_candidate_review_only": True,
            "automated_build_or_claim_authority": False,
            "external_submission_authority": False,
            "payout_or_cash_receipt_authority": False,
        },
    }


def _seal(result: dict[str, Any]) -> dict[str, Any]:
    # Unkeyed self-integrity only. A saved receipt cannot prove *live* availability.
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["observation_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


def evaluate_payload(
    bounty_id: int,
    payload: Any,
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Validate the *current* official API response. Safe pure transform.

    Call live_preflight for deployment. Passing a fabricated/stale payload here
    cannot authorize a build: every emitted receipt explicitly forbids it.
    """
    bounty_id = _bounty_id(bounty_id)
    result = _receipt(bounty_id, observed_at)
    if type(payload) is not dict or payload.get("ok") is not True:
        raise FranticPreflightError("official bounty response was not ok")
    bounty = payload.get("bounty")
    actions = payload.get("actions")
    if type(bounty) is not dict or type(actions) is not dict:
        raise FranticPreflightError("official response missing bounty/actions")
    if _bounty_id(bounty.get("number")) != bounty_id:
        raise FranticPreflightError("provider bounty identity mismatched request")

    price = _cents(bounty.get("price_cents"), "price_cents")
    fee = _cents(bounty.get("fee_cents"), "fee_cents", nullable=True)
    _amount_matches(price, bounty.get("price_usd"), "price")
    _amount_matches(fee, bounty.get("fee_usd"), "vendor fee")

    slots = bounty.get("claim_progress")
    if type(slots) is not dict:
        raise FranticPreflightError("claim_progress missing")
    capacity = _nonnegative_int(slots.get("capacity"), "capacity")
    occupied = _nonnegative_int(slots.get("occupied"), "occupied")
    available = _nonnegative_int(slots.get("available"), "available")
    if capacity <= 0 or occupied > capacity or available > capacity:
        raise FranticPreflightError("claim_progress exceeds capacity")
    if occupied + available != capacity:
        raise FranticPreflightError("claim_progress capacity does not reconcile")

    funded = bounty.get("funded")
    if type(funded) is not bool:
        raise FranticPreflightError("funded must be bool")
    cancellation = bounty.get("cancellation_status")
    if cancellation is not None and not isinstance(cancellation, str):
        raise FranticPreflightError("invalid cancellation_status")
    claim = actions.get("claim")
    if type(claim) is not dict:
        raise FranticPreflightError("missing claim action")
    state = claim.get("state")
    claim_available = claim.get("available")
    if state not in _ALLOWED_ACTIONS or type(claim_available) is not bool:
        raise FranticPreflightError("malformed claim action state")
    posting_status = bounty.get("posting_status")
    work_status = bounty.get("work_status")
    if not isinstance(posting_status, str) or not posting_status:
        raise FranticPreflightError("posting_status missing")
    if not isinstance(work_status, str) or not work_status:
        raise FranticPreflightError("work_status missing")

    result.update({
        "price_usd": format(Decimal(price) / 100, ".2f"),
        "vendor_fee_usd": (
            format(Decimal(fee) / 100, ".2f") if fee is not None else None
        ),
        "funded": funded,
        "capacity": capacity,
        "occupied_slots": occupied,
        "available_slots": available,
        "claim_gate_state": state,
        "claim_gate_available": claim_available,
        "posting_status": posting_status,
        "work_status": work_status,
    })
    reasons: list[str] = []
    if price < MIN_WORKER_PRICE_CENTS:
        reasons.append("BELOW_15_USD_WORKER_REWARD")
    if not funded:
        reasons.append("NOT_FUNDED")
    if available == 0:
        reasons.append("NO_AVAILABLE_CLAIM_SLOTS")
    if cancellation is not None:
        reasons.append("CANCELLED_OR_CANCELLING")
    if state in {"configured_closed", "unavailable"}:
        reasons.append("CLAIM_GATE_CLOSED")
    elif state == "requires_identity":
        reasons.append("REQUIRES_OPERATOR_IDENTITY_CHECK")
    elif not claim_available:
        reasons.append("CLAIM_ACTION_NOT_AVAILABLE")

    result["reason_codes"] = reasons
    # Even on a clean public response, a human/operator must reconfirm
    # eligibility, assignment, sponsor, conflicts and payout rail, then re-read
    # current source at claim time. Never grant implementation authority here.
    result["decision"] = (
        "READY_FOR_OPERATOR_RECHECK" if not reasons else "HOLD_" + reasons[0]
    )
    return _seal(result)


def live_preflight(bounty_id: int, *, session: Any = None) -> dict[str, Any]:
    """One bounded anonymous first-party read, with no retries or redirects."""
    bounty_id = _bounty_id(bounty_id)
    observed_at = _at_utc()
    result = _receipt(bounty_id, observed_at)
    url = result["source_url"]
    requester = session if session is not None else requests
    try:
        response = requester.get(
            url,
            timeout=10,
            headers={"Accept": "application/json", "User-Agent": "Commons-Frantic-Preflight/1"},
            allow_redirects=False,
        )
        if getattr(response, "status_code", None) != 200:
            result["decision"] = "HOLD_PROVIDER_HTTP_NOT_OK"
            result["reason_codes"] = ["PROVIDER_HTTP_NOT_OK"]
            return _seal(result)
        payload = response.json()
        return evaluate_payload(bounty_id, payload, observed_at=observed_at)
    except (requests.RequestException, TimeoutError, ValueError, TypeError, KeyError):
        result["decision"] = "HOLD_PROVIDER_READ_INVALID"
        result["reason_codes"] = ["PROVIDER_READ_INVALID"]
        return _seal(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Frantic claim slot/funding check; no claim is made."
    )
    parser.add_argument("bounty_id", type=int, help="Frantic canonical bounty number")
    args = parser.parse_args(argv)
    try:
        result = live_preflight(args.bounty_id)
    except FranticPreflightError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    # Zero only means a candidate warrants operator review, never a free claim.
    return 0 if result["decision"] == "READY_FOR_OPERATOR_RECHECK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
