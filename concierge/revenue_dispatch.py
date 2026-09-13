# SPDX-License-Identifier: MIT
"""Authoritative live paid-work dispatch wrapper with availability custody.

``revenue_intake.qualify_live_revenue_intake`` proves canonical reward,
provenance, assignment/competition, and other paid-work gates. This module adds
one independent final question before new implementation starts: does the
canonical issue thread already contain a stable explicit maintainer signal that
the opportunity has been accepted, awarded, filled, or cancelled?

This wrapper is intentionally additive while bounty_preflight has an active
source owner. It does not mutate issues, claims, submissions, wallets, or payout
state.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import requests

from concierge.bounty_availability import (
    BountyAvailabilityError,
    inspect_bounty_availability,
)
from concierge.revenue_intake import (
    RevenueIntakeInputError,
    qualify_live_revenue_intake,
)


class RevenueDispatchError(RuntimeError):
    """Raised when dispatch evidence cannot be composed safely."""


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
            "new_work_dispatch": True,
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
        "new_work_dispatch": False,
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
) -> dict[str, Any]:
    """Authorize NEW paid-work dispatch only when both live gates are clear."""
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

    # Do not spend extra provider reads trying to improve an already-held/rejected
    # opportunity. Availability is authoritative only for promotion to dispatch.
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
        result["dispatch_authority"] = {
            "intake": "canonical_live_intake",
            "availability": "not_needed",
            "new_work_dispatch": False,
        }
        return result

    availability = inspect_bounty_availability(
        repo,
        number,
        token,
        session=session,
        max_pages=max_pages,
    )
    return _availability_hold(intake, availability)


def format_summary(result: dict[str, Any]) -> str:
    availability = result.get("availability")
    reason = (
        availability.get("reason_code")
        if isinstance(availability, dict)
        else "missing"
    )
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"source={result.get('canonical_source_url') or 'none'} "
        f"availability_reason={reason or 'none'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_dispatch",
        description=(
            "Authorize new paid-work dispatch only after live revenue intake and "
            "stable maintainer availability checks."
        ),
    )
    parser.add_argument("repo", help="canonical GitHub repository in owner/name form")
    parser.add_argument("issue", type=int, help="canonical bounty issue number")
    parser.add_argument("--listing-url")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--saturation-threshold", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = qualify_available_live_revenue_intake(
            args.repo,
            args.issue,
            listing_url=args.listing_url,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
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
