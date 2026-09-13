# SPDX-License-Identifier: MIT
"""Paid-work intake gates with trusted-snapshot and live-canonical modes.

The snapshot API composes already-collected paid-work qualification and source
provenance evidence. For new dispatch decisions, :func:`qualify_live_revenue_intake`
is the authoritative path: it refreshes canonical GitHub issue, competition,
and claim-pressure state through ``bounty_preflight`` before provenance is
allowed to authorize dispatch.

Results are deliberately safe to log: raw issue, listing, contribution-term,
and comment text is never copied into the output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from concierge.bounty_audit import BountyAuditError
from concierge.bounty_preflight import BountyPreflightError, preflight_bounty
from concierge.bounty_qualification import QualificationInputError, qualify_dispatch
from concierge.source_provenance import (
    ProvenanceInputError,
    verify_source_provenance,
)


class RevenueIntakeInputError(ValueError):
    """Raised when combined intake evidence is structurally unreliable."""


_DISPOSITION_RANK = {"ACTIONABLE": 0, "HOLD": 1, "REJECT": 2}


def _combined_disposition(*values: str) -> str:
    try:
        return max(values, key=_DISPOSITION_RANK.__getitem__)
    except (KeyError, TypeError) as exc:
        raise RevenueIntakeInputError("gate returned an unknown disposition") from exc


def _combine_gate_results(
    qualification: dict[str, Any], provenance: dict[str, Any]
) -> dict[str, Any]:
    """Compose two safe gate results without copying source text."""
    if not isinstance(qualification, dict) or not isinstance(provenance, dict):
        raise RevenueIntakeInputError("gate result must be an object")

    try:
        disposition = _combined_disposition(
            qualification["disposition"], provenance["disposition"]
        )
        qualification_dispatch = qualification["dispatch"]
        provenance_dispatch = provenance["dispatch"]
    except KeyError as exc:
        raise RevenueIntakeInputError(
            "gate result was missing disposition or dispatch"
        ) from exc

    if type(qualification_dispatch) is not bool or type(provenance_dispatch) is not bool:
        raise RevenueIntakeInputError("gate dispatch must be boolean")

    reasons: list[dict[str, str]] = []
    reason_codes: list[str] = []
    seen_codes: set[str] = set()
    for gate_name, result in (
        ("qualification", qualification),
        ("provenance", provenance),
    ):
        gate_reasons = result.get("reasons", [])
        if not isinstance(gate_reasons, list):
            raise RevenueIntakeInputError("gate reasons must be a list")
        for reason in gate_reasons:
            if not isinstance(reason, dict):
                raise RevenueIntakeInputError("gate reason must be an object")
            try:
                code = reason["code"]
                severity = reason["severity"]
                message = reason["message"]
            except KeyError as exc:
                raise RevenueIntakeInputError("gate reason was incomplete") from exc
            if not all(type(value) is str for value in (code, severity, message)):
                raise RevenueIntakeInputError("gate reason fields must be strings")
            reasons.append(
                {
                    "gate": gate_name,
                    "code": code,
                    "severity": severity,
                    "message": message,
                }
            )
            qualified_code = f"{gate_name.upper()}:{code}"
            if qualified_code not in seen_codes:
                seen_codes.add(qualified_code)
                reason_codes.append(qualified_code)

    return {
        "disposition": disposition,
        "dispatch": (
            disposition == "ACTIONABLE"
            and qualification_dispatch
            and provenance_dispatch
        ),
        "canonical_source_url": provenance.get("use_source_url"),
        "reason_codes": reason_codes,
        "reasons": reasons,
        "qualification": {
            "disposition": qualification["disposition"],
            "dispatch": qualification_dispatch,
            "signals": qualification.get("signals", {}),
        },
        "provenance": {
            "disposition": provenance["disposition"],
            "dispatch": provenance_dispatch,
            "signals": provenance.get("signals", {}),
        },
    }


def qualify_revenue_intake(
    snapshot: dict[str, Any], *, saturation_threshold: int = 4
) -> dict[str, Any]:
    """Compose gates over a caller-supplied, already-collected snapshot.

    This path is intentionally retained for trusted replay/offline workflows.
    It does not refresh canonical GitHub state. New dispatch decisions should
    use :func:`qualify_live_revenue_intake`.
    """
    if not isinstance(snapshot, dict):
        raise RevenueIntakeInputError("snapshot must be an object")

    qualification = qualify_dispatch(
        snapshot, saturation_threshold=saturation_threshold
    )
    provenance = verify_source_provenance(snapshot)
    return _combine_gate_results(qualification, provenance)


def qualify_live_revenue_intake(
    repo: str,
    number: int,
    *,
    listing_url: str | None = None,
    token: str | None = None,
    session: Any = None,
    max_pages: int = 10,
    saturation_threshold: int = 4,
) -> dict[str, Any]:
    """Refresh canonical GitHub state before authorizing paid-work dispatch.

    ``bounty_preflight`` owns live issue, PR-competition, expiry, and claimant
    pressure reads. The returned canonical issue URL is then used as reward
    evidence for provenance; reward *presence* still comes from the live
    qualification result, so a bare issue URL cannot manufacture a paid offer.
    """
    kwargs: dict[str, Any] = {
        "max_pages": max_pages,
        "saturation_threshold": saturation_threshold,
    }
    if session is not None:
        kwargs["session"] = session

    preflight = preflight_bounty(repo, number, token, **kwargs)
    if not isinstance(preflight, dict):
        raise RevenueIntakeInputError("live preflight did not return an object")

    qualification = preflight.get("qualification")
    audit = preflight.get("canonical_audit")
    if not isinstance(qualification, dict) or not isinstance(audit, dict):
        raise RevenueIntakeInputError("live preflight was missing gate evidence")

    canonical_url = audit.get("issue_url")
    if type(canonical_url) is not str or not canonical_url.strip():
        raise RevenueIntakeInputError(
            "live preflight was missing canonical issue URL"
        )

    provenance = verify_source_provenance(
        {
            "listing_url": canonical_url if listing_url is None else listing_url,
            "reward_evidence_urls": [canonical_url],
            "canonical_audit": audit,
        }
    )
    return _combine_gate_results(qualification, provenance)


def format_summary(result: dict[str, Any]) -> str:
    """Format a compact safe dispatch receipt."""
    codes = ",".join(result.get("reason_codes", [])) or "none"
    source = result.get("canonical_source_url") or "none"
    return (
        f"disposition={result['disposition']} "
        f"dispatch={str(result['dispatch']).lower()} "
        f"source={source} reasons={codes}"
    )


def _load_snapshot(path: str) -> dict[str, Any]:
    if path == "-":
        import sys

        payload = json.load(sys.stdin)
    else:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RevenueIntakeInputError("snapshot JSON must contain an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m concierge.revenue_intake",
        description=(
            "Authorize paid-work dispatch from trusted snapshot evidence or "
            "fresh canonical GitHub state."
        ),
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        help=(
            "trusted normalized snapshot JSON path, or - for stdin; omit when "
            "using live --repo/--issue mode"
        ),
    )
    parser.add_argument(
        "--repo",
        help="canonical GitHub repository (owner/name) for live intake",
    )
    parser.add_argument(
        "--issue",
        type=int,
        help="canonical GitHub issue number for live intake",
    )
    parser.add_argument(
        "--listing-url",
        help="optional discovery listing URL; live mode still dispatches canonically",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="maximum canonical GitHub pages to inspect in live mode (default: 10)",
    )
    parser.add_argument(
        "--saturation-threshold",
        type=int,
        default=4,
        help="hold dispatch at this many attempts/open PRs (default: 4)",
    )
    parser.add_argument("--json", action="store_true", help="emit full result JSON")
    args = parser.parse_args(argv)

    live_requested = (
        args.repo is not None
        or args.issue is not None
        or args.listing_url is not None
    )
    if live_requested:
        if args.snapshot is not None:
            parser.error("snapshot path cannot be combined with live intake arguments")
        if args.repo is None or args.issue is None:
            parser.error("live intake requires both --repo and --issue")
    elif args.snapshot is None:
        parser.error("provide a trusted snapshot path or live --repo/--issue")

    try:
        if live_requested:
            result = qualify_live_revenue_intake(
                args.repo,
                args.issue,
                listing_url=args.listing_url,
                max_pages=args.max_pages,
                saturation_threshold=args.saturation_threshold,
            )
        else:
            snapshot = _load_snapshot(args.snapshot)
            result = qualify_revenue_intake(
                snapshot, saturation_threshold=args.saturation_threshold
            )
    except (
        OSError,
        json.JSONDecodeError,
        BountyAuditError,
        BountyPreflightError,
        QualificationInputError,
        ProvenanceInputError,
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
    if result["disposition"] == "HOLD":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
