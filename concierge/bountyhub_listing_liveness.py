# SPDX-License-Identifier: MIT
"""Hold BountyHub catalog targets whose canonical GitHub issue is not open.

A BountyHub listing carries the GitHub issue state as it stood when the listing
was created, and that mirror is not refreshed when the issue later closes. A
retained catalog row can therefore advertise an open, funded issue that upstream
closed months ago, and offline target selection has no way to notice: it reads
the mirrored state and nothing else. This module re-reads the canonical issue
through the existing availability authority before any target is treated as
actionable, and holds every target it cannot prove open.

Holds are per target and fail closed. A target whose canonical state cannot be
read is held with ``CANONICAL_STATE_UNREADABLE`` rather than aborting the sweep
or passing through unverified, so one gated or deleted repository cannot either
hide the remaining targets or promote itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

import requests

from concierge.bounty_availability import (
    BountyAvailabilityError,
    inspect_bounty_availability,
)
from concierge.bountyhub_catalog import SCHEMA as CATALOG_SCHEMA
from concierge.bountyhub_catalog import fetch_catalog, select_targets

SCHEMA = "bountyhub-listing-liveness/v1"
UNREADABLE = "CANONICAL_STATE_UNREADABLE"
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z")


def _target(value: Any) -> tuple[str, int]:
    if not isinstance(value, dict):
        raise ValueError("target must be an object")
    repo, number = value.get("repo"), value.get("number")
    if (not isinstance(repo, str) or not _REPO.fullmatch(repo)
            or repo.split("/")[1] in {".", ".."}):
        raise ValueError("invalid target repository")
    if type(number) is not int or number < 1:
        raise ValueError("invalid target issue number")
    return repo, number


def _listing_ids(selection: dict[str, Any], repo: str, number: int) -> list[str]:
    associations = selection.get("listing_ids_by_issue")
    if not isinstance(associations, dict):
        return []
    listing_ids = associations.get(f"{repo.casefold()}#{number}")
    if not isinstance(listing_ids, list):
        return []
    return sorted(item for item in listing_ids if isinstance(item, str))


def verify_listing_liveness(
    selection: dict[str, Any],
    token: str | None = None,
    *,
    session: Any = None,
    max_pages: int = 10,
) -> dict[str, Any]:
    """Re-read each selected target's canonical issue and hold the stale ones.

    ``selection`` is a ``bountyhub_catalog.select_targets`` envelope. An
    actionable verdict only means this gate found the canonical issue open with
    no terminal maintainer signal; it is not an assignment, a claim, or payment
    authority, and the listing's own funding evidence stays in the catalog
    report.
    """
    if not isinstance(selection, dict) or not isinstance(selection.get("targets"), list):
        raise ValueError("expected a bountyhub-catalog target selection")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")

    # One connection pool covers every target's issue and comment reads.
    if session is None:
        with requests.Session() as owned:
            return verify_listing_liveness(
                selection, token, session=owned, max_pages=max_pages
            )

    actionable: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for value in selection["targets"]:
        repo, number = _target(value)
        record: dict[str, Any] = {
            "repo": repo,
            "number": number,
            "issue_url": f"https://github.com/{repo}/issues/{number}",
            "listing_ids": _listing_ids(selection, repo, number),
            # Offline selection admits a row only while the mirrored listing
            # state reads open, so a closed canonical state is mirror staleness.
            "mirrored_issue_state": "open",
            "canonical_issue_state": None,
            "canonical_state_reason": None,
            "disposition": "HOLD",
            "dispatch": False,
            "reason_code": UNREADABLE,
            "signal_codes": [],
        }
        try:
            receipt = inspect_bounty_availability(
                repo, number, token, session=session, max_pages=max_pages
            )
        except BountyAvailabilityError:
            # The transport reason is not retained: an unreadable canonical
            # state is a hold whatever the provider said about it.
            held.append(record)
            continue
        clear = receipt.get("disposition") == "CLEAR" and receipt.get("dispatch") is True
        record.update(
            canonical_issue_state=receipt.get("issue_state"),
            canonical_state_reason=receipt.get("state_reason"),
            disposition=receipt.get("disposition"),
            dispatch=clear,
            reason_code=receipt.get("reason_code"),
            signal_codes=list(receipt.get("signal_codes") or []),
        )
        (actionable if clear else held).append(record)

    stale = [row for row in held if row["canonical_issue_state"] == "closed"]
    unreadable = [row for row in held if row["reason_code"] == UNREADABLE]
    return {
        "schema": SCHEMA,
        "actionable": actionable,
        "held": held,
        "target_count": len(actionable) + len(held),
        "actionable_count": len(actionable),
        "held_count": len(held),
        "stale_listing_count": len(stale),
        "stale_listing_ids": sorted(
            listing_id for row in stale for listing_id in row["listing_ids"]
        ),
        "unreadable_count": len(unreadable),
        "complete": not unreadable,
        "source_started_at": selection.get("source_started_at"),
        "source_completed_at": selection.get("source_completed_at"),
        "source_complete": selection.get("source_complete") is True,
        "authority": {
            "effect": "new_work_dispatch_only",
            "listing_state_is_canonical": False,
            "canonical_state_is_assignment": False,
            "canonical_state_is_payment_authority": False,
        },
    }


def format_summary(result: dict[str, Any]) -> str:
    return (
        f"targets={result['target_count']} "
        f"actionable={result['actionable_count']} "
        f"held={result['held_count']} "
        f"stale_listings={result['stale_listing_count']} "
        f"unreadable={result['unreadable_count']} "
        f"complete={str(result['complete']).lower()}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify", help="Verify a retained catalog report or an exported target selection"
    )
    verify.add_argument("snapshot", type=Path)
    chain = commands.add_parser(
        "chain", help="Collect the live catalog, select targets, then verify them"
    )
    chain.add_argument("--catalog-max-pages", type=int, default=10)
    chain.add_argument("--max-details", type=int, default=50)
    chain.add_argument("--page-size", type=int, default=100)
    for command in (verify, chain):
        command.add_argument(
            "--min-funded-usd", "--min-reward-usd", dest="min_funded_usd", default="50.00",
            help="Minimum USD in the selected reward basis (default: 50.00)",
        )
        command.add_argument(
            "--include-promised", action="store_true",
            help="Include resolved PROMISED pledges when applying the reward floor",
        )
        command.add_argument(
            "--max-pages", type=int, default=10,
            help="Maximum comment pages read per canonical issue (default: 10)",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "chain":
            catalog = fetch_catalog(
                max_pages=args.catalog_max_pages, max_details=args.max_details,
                page_size=args.page_size, minimum_total_usd=args.min_funded_usd,
                include_promised=args.include_promised,
            )
            selection = catalog["shortlist"]
        else:
            with args.snapshot.open(encoding="utf-8") as source:
                retained = json.load(source)
            if isinstance(retained, dict) and retained.get("schema") == CATALOG_SCHEMA:
                selection = select_targets(
                    retained, args.min_funded_usd, include_promised=args.include_promised
                )
            else:
                selection = retained
        result = verify_listing_liveness(selection, max_pages=args.max_pages)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(format_summary(result), file=sys.stderr)
        if not result["source_complete"]:
            print("PARTIAL: the retained catalog did not establish a complete shortlist",
                  file=sys.stderr)
        if not result["complete"]:
            print("HELD: at least one canonical issue state could not be read",
                  file=sys.stderr)
        return 0 if result["complete"] and result["source_complete"] else 2
    except (OSError, ValueError, KeyError, BountyAvailabilityError) as exc:
        print(f"bountyhub-listing-liveness: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
