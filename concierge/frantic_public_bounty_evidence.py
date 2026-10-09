# SPDX-License-Identifier: MIT
"""Read Frantic's first-party public bounty API into fail-closed dispatch evidence.

This is an observation tool, not a claim client or a dispatch permit. It does not
use identity credentials, update Frantic, or infer actual payments from a listing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit

SCHEMA = "frantic-public-claim-window-evidence/v1"
MAX_RESPONSE_BYTES = 256_000
SOURCE_ROOT = "https://gofrantic.com"


class FranticObservationError(ValueError):
    """An invalid, incomplete, or unavailable first-party snapshot."""


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise FranticObservationError(f"{name} must be an integer >= {minimum}")
    return value


def _money(cents: int) -> str:
    return format(Decimal(cents) / Decimal(100), ".2f")


def _as_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _timestamp(at: datetime | None) -> str:
    observed = at or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise FranticObservationError("observation clock must carry UTC offset")
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _firstparty_url(raw: Any, expected: str, field: str) -> None:
    if type(raw) is not str or raw.rstrip("/") != expected:
        raise FranticObservationError(f"{field} differs from canonical source URL")
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or parsed.hostname != "gofrantic.com" or parsed.query or parsed.fragment:
        raise FranticObservationError(f"{field} is not the expected first-party URL")


def evaluate_public_bounty(
    bounty_id: int, payload: dict[str, Any], *,
    raw_sha256: str, observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a first-party API response into safe, data-only claim evidence.

    A qualifying candidate is NOT a dispatch permit. Sponsor payment history,
    claimant account, first-party issue and duplicate checks are separate gates.
    """
    number = _integer(bounty_id, "bounty_id", minimum=1)
    if type(payload) is not dict or payload.get("ok") is not True:
        raise FranticObservationError("Frantic response is not a successful bounty object")
    if type(raw_sha256) is not str or len(raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in raw_sha256):
        raise FranticObservationError("raw response SHA-256 is required")
    bounty, actions = payload.get("bounty"), payload.get("actions")
    if type(bounty) is not dict or type(actions) is not dict:
        raise FranticObservationError("missing first-party bounty/actions objects")
    if _integer(bounty.get("number"), "bounty.number", minimum=1) != number:
        raise FranticObservationError("bounty number mismatches requested source")
    api_url = f"{SOURCE_ROOT}/v1/bounties/{number}"
    page_url = f"{SOURCE_ROOT}/bounties/{number}"
    _firstparty_url(bounty.get("api_url"), api_url, "bounty.api_url")
    _firstparty_url(bounty.get("page_url"), page_url, "bounty.page_url")

    price = _integer(bounty.get("price_cents"), "bounty.price_cents")
    fee = _integer(bounty.get("fee_cents"), "bounty.fee_cents")
    if type(bounty.get("funded")) is not bool:
        raise FranticObservationError("bounty.funded must be a boolean")
    progress = bounty.get("claim_progress")
    action = actions.get("claim")
    if type(progress) is not dict or type(action) is not dict:
        raise FranticObservationError("claim progress/action data missing")
    capacity = _integer(progress.get("capacity"), "claim_progress.capacity", minimum=1)
    occupied = _integer(progress.get("occupied"), "claim_progress.occupied")
    available = _integer(progress.get("available"), "claim_progress.available")
    if occupied + available > capacity:
        raise FranticObservationError("claim slots exceed documented capacity")
    is_action_available = action.get("available")
    state = action.get("state")
    if type(is_action_available) is not bool or state not in (
        "available", "requires_identity", "configured_closed", "unavailable"
    ):
        raise FranticObservationError("claim action availability/state is malformed")
    if state == "available" and not is_action_available:
        raise FranticObservationError("claim action claims available state but rejects availability")
    if state != "available" and is_action_available:
        raise FranticObservationError("claim action claims unavailable state with available=true")
    for name in ("posting_status", "work_status"):
        if type(bounty.get(name)) is not str or not bounty[name]:
            raise FranticObservationError(f"{name} is missing or malformed")

    reason_codes = []
    if not bounty["funded"]:
        reason_codes.append("UNFUNDED")
    if price < 1500:
        reason_codes.append("BELOW_15_USD_FLOOR")
    if available == 0:
        reason_codes.append("NO_FREE_CLAIM_SLOTS")
    if state == "configured_closed":
        reason_codes.append("CLAIM_GATE_CLOSED")
    elif state == "requires_identity":
        reason_codes.append("CLAIM_REQUIRES_AUTHENTICATED_IDENTITY")
    elif state == "unavailable":
        reason_codes.append("CLAIM_ACTION_UNAVAILABLE")
    if not is_action_available:
        reason_codes.append("CLAIM_ACTION_NOT_AVAILABLE")
    if bounty["posting_status"].lower() not in {"posted", "open", "active", "funded"}:
        reason_codes.append("POSTING_STATUS_NOT_CONFIRMED_OPEN")
    if bounty["work_status"].lower() not in {"open", "available", "claimable", "active"}:
        reason_codes.append("WORK_STATUS_NOT_CONFIRMED_OPEN")
    if bounty.get("cancellation_status") not in (None, "", "none"):
        reason_codes.append("CANCELLATION_STATUS_PRESENT")

    return {
        "schema": SCHEMA,
        "bounty_id": number,
        "source_url": api_url,
        "page_url": page_url,
        "observed_at": _timestamp(observed_at),
        "source_sha256": raw_sha256,
        "funded_usd": _money(price) if bounty["funded"] else "0.00",
        "listed_reward_usd": _money(price),
        "fee_usd": _money(fee),
        "fee_is_not_assumed_to_be_worker_deductible": True,
        "is_funded": bounty["funded"],
        "claim_capacity": capacity,
        "occupied_slots": occupied,
        "available_slots": available,
        "claim_gate_state": state,
        "claim_action_available": is_action_available,
        "posting_status": bounty["posting_status"],
        "work_status": bounty["work_status"],
        "candidate_for_independent_qualification": not reason_codes,
        "dispatch": False,
        "reason_codes": reason_codes or ["FURTHER_SPONSOR_AND_CLAIMANT_CHECKS_REQUIRED"],
        "payee_award_or_settlement_verified": False,
    }


class _FirstPartyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Refuse redirects rather than using a redirected page as source evidence.
        raise FranticObservationError("unexpected redirect from Frantic source")


def observe_public_bounty(bounty_id: int, *, timeout: int = 10) -> dict[str, Any]:
    bounty_id = _integer(bounty_id, "bounty_id", minimum=1)
    url = f"{SOURCE_ROOT}/v1/bounties/{bounty_id}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "bounty-concierge/claim-window-readonly"})
    try:
        with build_opener(_FirstPartyRedirect()).open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise FranticObservationError("expected application/json from Frantic")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise FranticObservationError("Frantic live JSON source unavailable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FranticObservationError("Frantic source exceeded size bound")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise FranticObservationError("invalid JSON from Frantic") from exc
    return evaluate_public_bounty(bounty_id, value, raw_sha256=_as_sha(raw))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bounty_id", type=int)
    parser.add_argument("--input", type=Path, help="Offline exact JSON response from official /v1/bounties/{id}")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.input is None:
            evidence = observe_public_bounty(args.bounty_id)
        else:
            raw = args.input.read_bytes()
            if len(raw) > MAX_RESPONSE_BYTES:
                raise FranticObservationError("Frantic input exceeded size bound")
            evidence = evaluate_public_bounty(
                args.bounty_id, json.loads(raw), raw_sha256=_as_sha(raw)
            )
        rendered = json.dumps(evidence, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (FranticObservationError, OSError, ValueError) as exc:
        print(f"frantic-public-bounty-evidence: HOLD: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
