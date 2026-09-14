# SPDX-License-Identifier: MIT
"""Resolve discovery listings and immediately run authoritative live intake.

This is the safe bridge between raw/mirrored paid-work discovery and Bounty
Concierge's canonical GitHub gates. Resolution never authorizes dispatch on its
own: only a unique canonical issue is handed to ``qualify_live_revenue_intake``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_preflight import BountyPreflightError
from concierge.bounty_qualification import QualificationInputError
from concierge.canonical_source_resolver import (
    CanonicalSourceInputError,
    resolve_canonical_source,
)
from concierge.revenue_intake import (
    RevenueIntakeInputError,
    qualify_live_revenue_intake,
)
from concierge.source_provenance import ProvenanceInputError


def _resolver_hold(resolution: dict[str, Any]) -> dict[str, Any]:
    codes = resolution.get("reason_codes", [])
    if not isinstance(codes, list) or not all(type(code) is str for code in codes):
        raise CanonicalSourceInputError("resolver reason_codes were malformed")
    qualified = [f"RESOLVER:{code}" for code in codes]
    reasons = [
        {
            "gate": "resolver",
            "code": code,
            "severity": "HOLD",
            "message": (
                "Discovery listing did not resolve to exactly one canonical "
                "GitHub issue; do not dispatch paid work from the mirror row."
            ),
        }
        for code in codes
    ]
    return {
        "disposition": "HOLD",
        "dispatch": False,
        "canonical_source_url": None,
        "reason_codes": qualified,
        "reasons": reasons,
        "resolver": resolution,
    }


def qualify_discovered_revenue_intake(
    listing: dict[str, Any],
    *,
    token: str | None = None,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Resolve one discovery listing, then refresh canonical state before dispatch.

    A missing or ambiguous canonical identity returns a safe HOLD without any
    GitHub live-intake call. A unique identity is not trusted by itself; it is
    passed to ``qualify_live_revenue_intake`` which remains authoritative for
    issue state, reward authority, formal assignment, claimant pressure,
    competition, expiry, and source provenance.
    """
    resolution = resolve_canonical_source(listing)
    if resolution.get("resolved") is not True:
        return _resolver_hold(resolution)

    repo = resolution.get("canonical_repo")
    number = resolution.get("issue_number")
    listing_url = listing.get("listing_url")
    if type(repo) is not str or not repo:
        raise CanonicalSourceInputError("resolver canonical_repo was malformed")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise CanonicalSourceInputError("resolver issue_number was malformed")
    if type(listing_url) is not str or not listing_url:
        raise CanonicalSourceInputError("listing_url was malformed after resolution")

    kwargs: dict[str, Any] = {
        "listing_url": listing_url,
        "token": token,
        "max_pages": max_pages,
        "saturation_threshold": saturation_threshold,
    }
    if session is not None:
        kwargs["session"] = session

    result = qualify_live_revenue_intake(repo, number, **kwargs)
    if not isinstance(result, dict):
        raise RevenueIntakeInputError("live revenue intake did not return an object")
    combined = dict(result)
    combined["resolver"] = resolution
    return combined


def format_summary(result: dict[str, Any]) -> str:
    codes = ",".join(result.get("reason_codes", [])) or "none"
    source = result.get("canonical_source_url") or "none"
    basis = result.get("resolver", {}).get("signals", {}).get(
        "resolution_basis", "NONE"
    )
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"basis={basis} source={source} reasons={codes}"
    )


def _load_listing(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if type(payload) is not dict:
        raise CanonicalSourceInputError("listing JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.discovered_revenue_intake",
        description=(
            "Resolve a discovery or mirror listing to one canonical GitHub issue "
            "and run fresh paid-work intake gates."
        ),
    )
    parser.add_argument("listing", help="listing JSON path, or - for stdin")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="maximum canonical GitHub pages to inspect (default: 10)",
    )
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="claim-pressure threshold; live intake enforces its policy ceiling",
    )
    parser.add_argument("--json", action="store_true", help="emit full result JSON")
    args = parser.parse_args(argv)

    try:
        listing = _load_listing(args.listing)
        result = qualify_discovered_revenue_intake(
            listing,
            max_pages=args.max_pages,
            saturation_threshold=args.saturation_threshold,
        )
    except (
        OSError,
        json.JSONDecodeError,
        BountyAuditError,
        BountyPreflightError,
        QualificationInputError,
        ProvenanceInputError,
        RevenueIntakeInputError,
        CanonicalSourceInputError,
        ValueError,
    ) as exc:
        parser.error(str(exc))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_summary(result))

    if result["dispatch"]:
        return 0
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
