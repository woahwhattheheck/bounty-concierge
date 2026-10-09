# SPDX-License-Identifier: MIT
"""Exclude assigned exclusive listings from an existing BountyHub shortlist.

This is an optional, offline fresh-intake view. The ordinary targets command
continues to include existing assignments for their current owners to recover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from concierge.bountyhub_exclusions import unique_exclusion_fields


def _exclude_assigned_exclusive(
    report: dict[str, Any], selected: dict[str, Any], *,
    max_open_claims: int | None = None,
) -> dict[str, Any]:
    """Filter listing IDs, preserving other eligible listings on the same issue."""
    if max_open_claims is not None and (
        type(max_open_claims) is not int or not 0 <= max_open_claims <= 100000
    ):
        raise ValueError("max_open_claims must be an integer from 0 to 100000")
    if report.get("schema") == "bountyhub-target-refresh/v1":
        rows = [record.get("listing") for record in report["records"]]
    else:
        rows = report["listings"]
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue  # Failed refresh records have no listing; the reducer handles them.
        listing_id = row.get("listing_id")
        if not isinstance(listing_id, str):
            raise ValueError("invalid retained listing identity")
        if listing_id in by_id:
            raise ValueError("repeated retained listing identity")
        by_id[listing_id] = row

    associations: dict[str, list[str]] = {}
    excluded = []
    open_claim_excluded: list[dict[str, Any]] = []
    for issue_key, listing_ids in selected["listing_ids_by_issue"].items():
        remaining = []
        for listing_id in listing_ids:
            row = by_id.get(listing_id)
            if row is None or f"{row['repo'].casefold()}#{row['number']}" != issue_key:
                raise ValueError("selected listing disagrees with retained evidence")
            assignment = row.get("assignment_type")
            if not isinstance(assignment, str) or not assignment:
                raise ValueError("missing retained assignment type")
            if type(row.get("has_assignee")) is not bool:
                raise ValueError("missing retained assignment flag")
            if assignment.casefold() == "exclusive" and row["has_assignee"]:
                excluded.append({
                    "listing_id": listing_id,
                    "repo": row["repo"],
                    "number": row["number"],
                    "reason": "exclusive_listing_already_assigned",
                })
            elif max_open_claims is not None:
                open_claim_count = row.get("open_claim_count")
                if type(open_claim_count) is not int or open_claim_count < 0:
                    raise ValueError("missing retained open claim count")
                if open_claim_count > max_open_claims:
                    open_claim_excluded.append({
                        "listing_id": listing_id,
                        "repo": row["repo"],
                        "number": row["number"],
                        "reason": "open_claim_competition_over_threshold",
                        "open_claim_count": open_claim_count,
                    })
                else:
                    remaining.append(listing_id)
            else:
                remaining.append(listing_id)
        if remaining:
            associations[issue_key] = remaining

    result = dict(selected)
    result["listing_ids_by_issue"] = associations
    result["targets"] = [
        target for target in selected["targets"]
        if f"{target['repo'].casefold()}#{target['number']}" in associations
    ]
    result["fresh_intake_policy"] = "exclude_assigned_exclusive_listings"
    result["assigned_exclusive_exclusions"] = excluded
    result["assigned_exclusive_exclusion_count"] = len(excluded)
    if max_open_claims is not None:
        result["fresh_intake_policy"] = "exclude_assigned_exclusive_and_crowded_listings"
        result["max_open_claims"] = max_open_claims
        result["open_claim_exclusion_count"] = len(open_claim_excluded)
        result["open_claim_exclusions"] = open_claim_excluded
    result["swarm_reservation_schema"] = "swarm-custody-reservation/v1"
    result["swarm_take_schema"] = "swarm-claim-take/v1"
    annotated_targets = []
    for retained_target in result["targets"]:
        target = dict(retained_target)
        from tools import swarm_claim_take

        canonicalization = swarm_claim_take._reservation_identity(
            {
                "repository": target["repo"],
                "issue_number": target["number"],
            },
            lane="build",
            mutation_resource=None,
        )
        work_key = canonicalization["reservation_key"]
        digest = hashlib.sha256(work_key.encode("utf-8")).hexdigest()
        target["swarm_reservation"] = {
            "schema": result["swarm_reservation_schema"],
            "work_key": work_key,
            "branch": f"swarm-custody/v1/{digest}",
            "tool": "tools/swarm_claim_reservation.py",
            "take_schema": result["swarm_take_schema"],
            "take_tool": "tools/swarm_claim_take.py",
            "take_protocol": "canonicalize_then_reserve_before_slack_take",
            "take_lane": canonicalization["lane"],
            "take_resource": canonicalization["resource"],
            "canonicalization_status": canonicalization["status"],
        }
        annotated_targets.append(target)
    result["targets"] = annotated_targets
    # These rows are discovery leads, never implementation authority.  Keep the
    # rich reservation metadata for coordination, but also expose a projection
    # whose schema is accepted verbatim by bounty_capture_batch so callers do
    # not need to hand-strip fields before the live canonical preflight.
    result["dispatch_status"] = "LEAD"
    result["green_authorized"] = False
    result["requires_canonical_preflight"] = True
    result["requires_work_order_lease"] = True
    result["requires_swarm_reservation"] = True
    result["canonical_preflight_candidates"] = {
        "candidates": [
            {
                key: target[key]
                for key in ("repo", "number", "submission_target")
                if key in target
            }
            for target in annotated_targets
        ]
    }
    return result


def select_fresh_targets(
    report: dict[str, Any], minimum_funded_usd: str = "15.00", *,
    include_promised: bool = False,
    submission_targets: dict[str, Any] | None = None,
    excluded_issues: dict[str, Any] | None = None,
    max_open_claims: int | None = None,
) -> dict[str, Any]:
    """Reuse funding, identity, refresh and canonical-exclusion rules unchanged."""
    from concierge.bountyhub_catalog import select_targets

    selected = select_targets(
        report, minimum_funded_usd, include_promised=include_promised,
        submission_targets=submission_targets, excluded_issues=excluded_issues,
    )
    return _exclude_assigned_exclusive(report, selected, max_open_claims=max_open_claims)


_MAX_INPUT_BYTES = 32 * 1024 * 1024


def _load(path: Path | None) -> Any:
    """Bound retained input bytes before parsing potentially huge JSON trees."""
    if path is None:
        return None
    with path.open("rb") as source:
        raw = source.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("retained BountyHub input exceeds 32 MiB")
    return json.loads(raw, object_pairs_hook=unique_exclusion_fields)


def _same_destination(left: Path, right: Path) -> bool:
    """Catch lexical, symlink, and existing hardlink aliases before writing."""
    if left.resolve() == right.resolve():
        return True
    return left.exists() and right.exists() and left.samefile(right)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Retained catalog or target-refresh JSON")
    parser.add_argument("--min-funded-usd", default="15.00")
    parser.add_argument("--include-promised", action="store_true")
    parser.add_argument(
        "--max-open-claims", type=int,
        help="Optional discovery-only filter: skip listings with more open claims than N",
    )
    parser.add_argument("--submission-targets", type=Path)
    parser.add_argument("--exclude-issues", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--preflight-output", type=Path,
        help=(
            "Optional exact repo/number envelope for bounty_capture_batch; "
            "still requires live canonical preflight before GREEN dispatch"
        ),
    )
    args = parser.parse_args(argv)
    try:
        from concierge.bountyhub_catalog import _emit_json

        result = select_fresh_targets(
            _load(args.report), args.min_funded_usd,
            include_promised=args.include_promised,
            submission_targets=_load(args.submission_targets),
            excluded_issues=_load(args.exclude_issues),
            max_open_claims=args.max_open_claims,
        )
        # Never overwrite the very evidence or options just used for this dispatch.
        # Resolve symlinked paths and existing hardlinks, not only identical names.
        inputs = [p for p in (args.report, args.submission_targets, args.exclude_issues)
                  if p is not None]
        outputs = [p for p in (args.output, args.preflight_output) if p is not None]
        for offset, destination in enumerate(outputs):
            if any(_same_destination(destination, other)
                   for other in inputs + outputs[:offset]):
                raise ValueError("BountyHub output aliases an input or another output")
        _emit_json(result, args.output)
        if args.preflight_output is not None:
            _emit_json(result["canonical_preflight_candidates"], args.preflight_output)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # Do not echo paths, raw retained fields or JSON payloads on input failure.
        print("bountyhub-fresh-targets: invalid input or output destination", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
