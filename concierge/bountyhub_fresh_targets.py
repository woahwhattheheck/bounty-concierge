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
    report: dict[str, Any], selected: dict[str, Any]
) -> dict[str, Any]:
    """Filter qualifying listing IDs, not entire issues with mixed listings."""
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
    result["swarm_reservation_schema"] = "swarm-custody-reservation/v1"
    annotated_targets = []
    for retained_target in result["targets"]:
        target = dict(retained_target)
        work_key = (
            f"bountyhub:{target['repo'].casefold()}#{target['number']}"
        )
        digest = hashlib.sha256(work_key.encode("utf-8")).hexdigest()
        target["swarm_reservation"] = {
            "schema": result["swarm_reservation_schema"],
            "work_key": work_key,
            "branch": f"swarm-custody/v1/{digest}",
            "tool": "tools/swarm_claim_reservation.py",
        }
        annotated_targets.append(target)
    result["targets"] = annotated_targets
    return result


def select_fresh_targets(
    report: dict[str, Any], minimum_funded_usd: str = "15.00", *,
    include_promised: bool = False,
    submission_targets: dict[str, Any] | None = None,
    excluded_issues: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reuse funding, identity, refresh and canonical-exclusion rules unchanged."""
    from concierge.bountyhub_catalog import select_targets

    selected = select_targets(
        report, minimum_funded_usd, include_promised=include_promised,
        submission_targets=submission_targets, excluded_issues=excluded_issues,
    )
    return _exclude_assigned_exclusive(report, selected)


def _load(path: Path | None) -> Any:
    if path is None:
        return None
    with path.open(encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=unique_exclusion_fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Retained catalog or target-refresh JSON")
    parser.add_argument("--min-funded-usd", default="15.00")
    parser.add_argument("--include-promised", action="store_true")
    parser.add_argument("--submission-targets", type=Path)
    parser.add_argument("--exclude-issues", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        from concierge.bountyhub_catalog import _emit_json

        result = select_fresh_targets(
            _load(args.report), args.min_funded_usd,
            include_promised=args.include_promised,
            submission_targets=_load(args.submission_targets),
            excluded_issues=_load(args.exclude_issues),
        )
        _emit_json(result, args.output)
    except (OSError, ValueError, TypeError, KeyError):
        # Do not echo paths, raw retained fields or JSON payloads on input failure.
        print("bountyhub-fresh-targets: invalid input or output destination", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
