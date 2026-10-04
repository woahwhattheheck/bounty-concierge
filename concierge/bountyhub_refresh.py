# SPDX-License-Identifier: MIT
"""Refresh explicit known BountyHub listings without repeating catalog discovery.

The separate receipt schema deliberately cannot masquerade as a fresh catalog.
No claim, publication, account change, provider retry, or payment is performed.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import requests

from concierge import bountyhub_catalog as catalog

SCHEMA = "bountyhub-target-refresh/v1"


def refresh_listings(snapshot: dict[str, Any], listing_ids: list[str], *,
                     previous_refresh: dict[str, Any] | None = None,
                     max_requests: int | None = None,
                     session: Any = None) -> dict[str, Any]:
    """Refresh known details within an explicit 0-100 HTTP request allowance.

    Input is a fully traversed retained catalog, not a source of fresh funding.
    Pass the last refresh receipt on subsequent calls to enforce its cooldown.
    The default allowance is one request per distinct identity; redirects count.
    The caller owns a supplied Session; all requests use the existing transport.
    """
    # Early v1 reports omitted page_size. No page reads occur here: use only
    # the existing validator's upper bound, never infer or export a past size.
    validation = snapshot
    if isinstance(snapshot, dict) and "page_size" not in snapshot:
        validation = {**snapshot, "page_size": 100}
    retained = catalog._resume_input(validation)
    if not isinstance(listing_ids, list) or not 1 <= len(listing_ids) <= 100:
        raise ValueError("provide between 1 and 100 explicit listing IDs")
    if any(not isinstance(item, str) or not catalog._ID.fullmatch(item) for item in listing_ids):
        raise ValueError("invalid listing identity")
    selected = list(dict.fromkeys(listing_ids))
    if max_requests is None:
        max_requests = len(selected)
    if type(max_requests) is not int or not 0 <= max_requests <= 100:
        raise ValueError("max_requests must be between 0 and 100")
    rows = {row["listing_id"]: row for row in retained["listings"]}
    if any(item not in rows for item in selected):
        raise ValueError("requested listing is absent from the retained catalog")
    scope = snapshot.get("shortlist")
    if not isinstance(scope, dict):
        raise ValueError("missing retained shortlist scope")
    basis = scope.get("reward_basis")
    if basis not in (None, "reported_funded_plus_promised"):
        raise ValueError("invalid retained reward basis")
    include_promised = basis is not None
    floor_key = "minimum_reward_usd" if include_promised else "minimum_funded_usd"
    minimum = retained["minimum_total_usd"]
    if catalog._amount(scope.get(floor_key)) != catalog._amount(minimum):
        raise ValueError("retained shortlist floor disagrees with collection")
    try:
        source_digest = hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        raise ValueError("retained catalog must contain finite JSON values") from None
    started_at = catalog._now()
    now = catalog._instant(started_at)
    cooldown_sources = [retained]
    if previous_refresh is not None:
        if (not isinstance(previous_refresh, dict)
                or previous_refresh.get("schema") != SCHEMA
                or previous_refresh.get("source_sha256") != source_digest):
            raise ValueError("previous refresh must reference the same retained catalog")
        cooldown_sources.append(previous_refresh)
    for source in cooldown_sources:
        completed = catalog._instant(source.get("completed_at"))
        delay = source.get("retry_after_seconds")
        if delay is not None and (type(delay) is not int or not 0 <= delay < 10**12):
            raise ValueError("invalid retained retry guidance")
        if now < completed:
            raise ValueError("refresh time predates retained observation")
        if delay is not None and (now - completed).total_seconds() < delay:
            raise ValueError("retained Retry-After cooldown has not elapsed")

    report: dict[str, Any] = {
        "schema": SCHEMA, "source_sha256": source_digest,
        "source_started_at": snapshot["started_at"],
        "source_completed_at": snapshot["completed_at"],
        "catalog_observed_through": retained["catalog_observed_through"],
        "catalog_refreshed": False, "source_page_size": snapshot.get("page_size"),
        "scope": "requested_listing_ids_only",
        "started_at": started_at, "completed_at": None,
        "requested_listing_ids": selected, "input_listing_count": len(listing_ids),
        "request_limit": max_requests, "requests_made": 0, "details_fetched": 0,
        "rate_limited": False, "retry_after_seconds": None,
        "details_complete": True, "complete": False,
        "records": [], "errors": [],
    }
    fresh_rows: list[dict[str, Any]] = []
    stopped = False
    with requests.Session() if session is None and max_requests else nullcontext(session) as client:
        for listing_id in selected:
            expected = rows[listing_id]
            record: dict[str, Any] = {
                "listing_id": listing_id, "repo": expected["repo"],
                "number": expected["number"], "source_url": expected["source_url"],
                "status": "NOT_ATTEMPTED", "started_at": None, "completed_at": None,
                "listing": None,
            }
            report["records"].append(record)
            if stopped or report["requests_made"] >= max_requests:
                report["details_complete"] = False
                continue
            record["started_at"] = catalog._now()
            try:
                payload = catalog._get(client, expected["source_url"], report)
                report["details_fetched"] += 1
                fresh = catalog._detail(payload, expected)
                record.update(status="COMPLETE", listing=fresh)
                fresh_rows.append(fresh)
            except catalog._ReadFailure as exc:
                record["status"] = "READ_FAILED"
                catalog._record_failure(report, exc, "detail", listing_id=listing_id)
                report["details_complete"] = False
                stopped = exc.code != "HTTP_ERROR" or exc.status not in {404, 410}
            except ValueError:
                record["status"] = "INVALID_DETAIL"
                report["errors"].append({
                    "phase": "detail", "listing_id": listing_id, "code": "INVALID_DETAIL",
                })
                report["details_complete"] = False
            finally:
                record["completed_at"] = catalog._now()
    report["completed_at"] = catalog._now()
    # Reuse the existing economic selector, but export only this explicit scope.
    # Neither retained COMPLETE details nor failed-read fallbacks enter it.
    shortlist = catalog.select_targets({
        "schema": catalog.SCHEMA, "listings": fresh_rows,
        "complete": report["details_complete"],
        "started_at": report["started_at"], "completed_at": report["completed_at"],
    }, minimum, include_promised=include_promised)
    report.update(
        candidates=shortlist["targets"],
        listing_ids_by_issue=shortlist["listing_ids_by_issue"],
        unresolved_funding_count=shortlist["unresolved_funding_count"],
        complete=shortlist["source_complete"],
    )
    if include_promised:
        report.update(reward_basis=basis, minimum_reward_usd=minimum)
    else:
        report["minimum_funded_usd"] = minimum
    return report


def _load(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("input exceeds 4 MiB")
    return json.loads(raw, object_pairs_hook=catalog._unique_target_fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--listing-id", action="append", required=True, dest="listing_ids")
    parser.add_argument("--max-requests", type=int,
                        help="HTTP request allowance, 0-100; defaults to the distinct listing count")
    parser.add_argument("--previous-refresh", type=Path,
                        help="Last receipt for this catalog; enforces its Retry-After cooldown")
    args = parser.parse_args(argv)
    try:
        result = refresh_listings(
            _load(args.snapshot), args.listing_ids,
            previous_refresh=_load(args.previous_refresh) if args.previous_refresh else None,
            max_requests=args.max_requests,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["complete"]:
            print("PARTIAL: requested listing details or funding remain unresolved", file=sys.stderr)
        return 0 if result["complete"] else 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"bountyhub-refresh: {type(exc).__name__}: {catalog._input_error_message(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
