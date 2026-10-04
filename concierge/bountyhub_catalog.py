# SPDX-License-Identifier: MIT
"""Read BountyHub's public catalog and export existing-preflight targets.

Only allowlisted listing fields and aggregate pledge/claim counts leave this
module. A funded listing is discovery evidence, not our assignment or payment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import requests

API = "https://api.bountyhub.dev/api/bounties"
SCHEMA = "bountyhub-catalog/v1"
_MONEY = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,2})?\Z")
_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _amount(value: Any) -> Decimal:
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("amount must be a nonnegative bounded decimal string")
    return Decimal(value)


def _money(value: Decimal) -> str:
    return format(value, ".2f")


def _boolean(record: dict[str, Any], key: str) -> bool:
    value = record.get(key)
    if type(value) is not bool:
        raise ValueError(f"invalid {key} flag")
    return value


def _row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("listing must be an object")
    listing_id, repo, number = value.get("id"), value.get("repositoryFullName"), value.get("issueNumber")
    if not isinstance(listing_id, str) or not _ID.fullmatch(listing_id):
        raise ValueError("invalid listing identity")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo.split("/")[1] in {".", ".."}:
        raise ValueError("invalid repository identity")
    if type(number) is not int or number < 1:
        raise ValueError("invalid issue number")
    issue_url = f"https://github.com/{repo}/issues/{number}"
    if not isinstance(value.get("htmlURL"), str) or value["htmlURL"].casefold() != issue_url.casefold():
        raise ValueError("listing issue identities disagree")
    title, state, assignment = value.get("title"), value.get("issueState"), value.get("assignmentType")
    if not all(isinstance(item, str) and item for item in (title, state, assignment)):
        raise ValueError("missing listing metadata")
    row = {
        "listing_id": listing_id, "source_url": f"{API}/{listing_id}",
        "repo": repo, "number": number, "issue_url": issue_url,
        "title": title[:2048], "issue_state": state.lower(),
        "assignment_type": assignment[:64], "has_assignee": value.get("assignee") is not None,
        "claimed": _boolean(value, "claimed"), "retracted": _boolean(value, "retracted"),
        "solved": _boolean(value, "solved"), "frozen": _boolean(value, "isFrozen"),
        "deleted": value.get("deletedAt") is not None,
        "advertised_total_usd": _money(_amount(value.get("totalAmount"))),
        "funding_status": "NOT_REQUESTED", "reported_funded_usd": None,
        "reported_promised_usd": None, "other_payment_status_usd": None,
        "payout_marked_usd": None, "active_pledge_count": None,
        "claim_count": None, "open_claim_count": None,
    }
    language = value.get("language")
    if isinstance(language, str) and language.strip():
        row["language"] = language.strip()[:128]
    return row


def _active(row: dict[str, Any]) -> bool:
    return row.get("issue_state") == "open" and all(
        row.get(key) is False for key in ("claimed", "retracted", "solved", "frozen", "deleted")
    )


def _detail(value: Any, expected: dict[str, Any]) -> dict[str, Any]:
    row = _row(value)
    if "language" not in value and isinstance(expected.get("language"), str):
        row["language"] = expected["language"][:128]
    if (row["listing_id"], row["repo"].casefold(), row["number"]) != (
        expected["listing_id"], expected["repo"].casefold(), expected["number"]
    ):
        raise ValueError("detail belongs to a different listing or issue")
    pledges, claims = value.get("pledges"), value.get("claims")
    if not isinstance(pledges, list) or not isinstance(claims, list):
        raise ValueError("detail lacks pledge or claim records")
    funded = promised = other = payout = Decimal(0)
    active_count = 0
    for pledge in pledges:
        if not isinstance(pledge, dict):
            raise ValueError("invalid pledge")
        if _boolean(pledge, "retracted") or pledge.get("deletedAt") is not None:
            continue
        amount = _amount(pledge.get("amount"))
        status = pledge.get("paymentStatus")
        if not isinstance(status, str) or not status:
            raise ValueError("missing pledge payment status")
        active_count += 1
        if status == "PAID":
            funded += amount
        elif status == "PROMISED":
            promised += amount
        else:
            other += amount
        if _boolean(pledge, "isPaid"):
            payout += amount
    # The pledge array already includes the creator's original pledge. Adding
    # listing.amount again double-counts it; amountPaid also includes fees.
    if funded + promised + other != _amount(row["advertised_total_usd"]):
        raise ValueError("pledge total disagrees with the listing total")
    if any(not isinstance(claim, dict) for claim in claims):
        raise ValueError("invalid claim record")
    current_claims = [claim for claim in claims if claim.get("deletedAt") is None]
    row.update(
        funding_status="COMPLETE", reported_funded_usd=_money(funded),
        reported_promised_usd=_money(promised), other_payment_status_usd=_money(other),
        payout_marked_usd=_money(payout), active_pledge_count=active_count,
        claim_count=len(current_claims),
        open_claim_count=sum(_boolean(claim, "isOpen") and claim.get("rejectedAt") is None
                             for claim in current_claims),
    )
    return row


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    if value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value)
    try:
        instant = parsedate_to_datetime(value)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((instant - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class _ReadFailure(Exception):
    def __init__(self, code: str, status: int | None = None, retry_after: int | None = None):
        self.code, self.status, self.retry_after = code, status, retry_after


def _get(session: Any, url: str, report: dict[str, Any], **params: Any) -> Any:
    report["requests_made"] += 1
    try:
        response = session.get(url, params=params or None, timeout=20)
    except requests.RequestException as exc:
        raise _ReadFailure(type(exc).__name__) from None
    try:
        delay = _retry_after(response.headers.get("Retry-After"))
        status = response.status_code
        if status != 200:
            raise _ReadFailure("HTTP_ERROR", status, delay)
        try:
            return response.json()
        except ValueError:
            raise _ReadFailure("INVALID_JSON", status) from None
    finally:
        response.close()


def select_targets(report: dict[str, Any], minimum_funded_usd: str = "25.00", *,
                   include_promised: bool = False) -> dict[str, Any]:
    """Reduce a retained report without refreshing its time or making requests."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or not isinstance(report.get("listings"), list):
        raise ValueError("expected a bountyhub-catalog/v1 report")
    floor = _amount(minimum_funded_usd)
    targets: dict[tuple[str, int], dict[str, Any]] = {}
    associations: dict[str, list[str]] = {}
    unresolved = 0
    for row in report["listings"]:
        if not isinstance(row, dict):
            raise ValueError("invalid retained listing")
        if not _active(row):
            continue
        if _amount(row["advertised_total_usd"]) < floor:
            continue
        if row.get("funding_status") != "COMPLETE":
            unresolved += 1
            continue
        # Never treat reported prior payout as funding available for new work.
        if _amount(row["payout_marked_usd"]) != 0 or _amount(row["other_payment_status_usd"]) != 0:
            unresolved += 1
            continue
        reward = _amount(row["reported_funded_usd"])
        if include_promised:
            reward += _amount(row["reported_promised_usd"])
        if reward < floor:
            continue
        repo, number, listing_id = row.get("repo"), row.get("number"), row.get("listing_id")
        if (not isinstance(repo, str) or not _REPO.fullmatch(repo)
                or type(number) is not int or number < 1
                or not isinstance(listing_id, str) or not _ID.fullmatch(listing_id)):
            raise ValueError("invalid retained target identity")
        key = (repo.casefold(), number)
        targets.setdefault(key, {"repo": repo, "number": number})
        associations.setdefault(f"{repo.casefold()}#{number}", []).append(listing_id)
    result = {
        "targets": list(targets.values()), "listing_ids_by_issue": associations,
        "minimum_funded_usd": _money(floor), "unresolved_funding_count": unresolved,
        "source_started_at": report.get("started_at"),
        "source_completed_at": report.get("completed_at"),
        "source_complete": report.get("complete") is True and unresolved == 0,
    }
    if include_promised:
        del result["minimum_funded_usd"]
        result.update(reward_basis="reported_funded_plus_promised", minimum_reward_usd=_money(floor))
    return result


def fetch_catalog(*, max_pages: int = 10, max_details: int = 50, page_size: int = 100,
                  minimum_total_usd: str = "25.00", include_promised: bool = False,
                  session: Any = None) -> dict[str, Any]:
    """Collect once with bounded reads; retain partial progress and never retry."""
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be between 1 and 100")
    if type(max_details) is not int or not 0 <= max_details <= 100:
        raise ValueError("max_details must be between 0 and 100")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    floor = _amount(minimum_total_usd)
    if session is None:
        with requests.Session() as owned:
            return fetch_catalog(max_pages=max_pages, max_details=max_details,
                                 page_size=page_size, minimum_total_usd=minimum_total_usd,
                                 include_promised=include_promised, session=owned)
    report: dict[str, Any] = {
        "schema": SCHEMA, "source_url": API, "started_at": _now(),
        "completed_at": None, "complete": False, "catalog_complete": False,
        "details_complete": True, "pages_fetched": 0, "details_fetched": 0,
        "requests_made": 0, "minimum_total_usd": _money(floor),
        "max_pages": max_pages, "max_details": max_details, "page_size": page_size,
        "rate_limited": False,
        "retry_after_seconds": None, "errors": [], "listings": [],
    }
    seen: set[str] = set()
    stopped = False

    def failed(exc: _ReadFailure, phase: str, **identity: Any) -> None:
        report["errors"].append({"phase": phase, **identity, "code": exc.code, "http_status": exc.status})
        report["rate_limited"] = exc.status == 429 or (exc.status == 403 and exc.retry_after is not None)
        report["retry_after_seconds"] = exc.retry_after

    for page in range(1, max_pages + 1):
        try:
            payload = _get(session, API, report, page=page, limit=page_size)
            if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                    or type(payload.get("hasNextPage")) is not bool):
                raise ValueError("invalid catalog page")
            report["pages_fetched"] += 1
            for raw in payload["data"]:
                row = _row(raw)
                if row["listing_id"] in seen:
                    raise ValueError("listing repeated during pagination")
                seen.add(row["listing_id"])
                report["listings"].append(row)
            if not payload["hasNextPage"]:
                report["catalog_complete"] = True
                break
            if not payload["data"]:
                raise ValueError("empty page reports a successor")
        except _ReadFailure as exc:
            failed(exc, "catalog", page=page)
            stopped = True
            break
        except ValueError:
            report["errors"].append({"phase": "catalog", "page": page, "code": "INVALID_PAGE"})
            stopped = True
            break
    if not report["catalog_complete"] and not stopped:
        report["errors"].append({"phase": "catalog", "code": "PAGE_LIMIT"})
    detail_attempts = 0
    for index, row in enumerate(report["listings"]):
        if not _active(row) or _amount(row["advertised_total_usd"]) < floor:
            continue
        if stopped or detail_attempts >= max_details:
            row["funding_status"] = "NOT_ATTEMPTED" if stopped else "DETAIL_LIMIT"
            report["details_complete"] = False
            continue
        try:
            detail_attempts += 1
            payload = _get(session, row["source_url"], report)
            report["details_fetched"] += 1
            report["listings"][index] = _detail(payload, row)
        except _ReadFailure as exc:
            row["funding_status"] = "READ_FAILED"
            failed(exc, "detail", listing_id=row["listing_id"])
            report["details_complete"] = False
            # A listing removed after catalog collection does not invalidate
            # later listings. Its failed read still consumes the detail budget.
            stopped = exc.code != "HTTP_ERROR" or exc.status not in {404, 410}
        except ValueError:
            row["funding_status"] = "INVALID_DETAIL"
            report["errors"].append({"phase": "detail", "listing_id": row["listing_id"], "code": "INVALID_DETAIL"})
            report["details_complete"] = False
    report["completed_at"] = _now()
    report["complete"] = report["catalog_complete"] and report["details_complete"]
    report["listing_count"] = len(report["listings"])
    report["shortlist"] = select_targets(report, minimum_total_usd, include_promised=include_promised)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Read the public catalog and selected pledge details")
    collect.add_argument("--max-pages", type=int, default=10)
    collect.add_argument("--max-details", type=int, default=50)
    collect.add_argument("--page-size", type=int, default=100)
    targets = commands.add_parser("targets", help="Export targets from a retained catalog with no provider reads")
    targets.add_argument("snapshot", type=Path)
    for command in (collect, targets):
        command.add_argument(
            "--min-funded-usd", "--min-reward-usd", dest="min_funded_usd", default="25.00",
            help="Minimum USD in the selected reward basis: funded by default, funded plus promised "
                 "with --include-promised (default: 25.00)",
        )
        command.add_argument(
            "--include-promised", action="store_true",
            help="Include resolved PROMISED pledges with funded pledges when applying the reward floor",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            result = fetch_catalog(max_pages=args.max_pages, max_details=args.max_details,
                                   page_size=args.page_size,
                                   minimum_total_usd=args.min_funded_usd,
                                   include_promised=args.include_promised)
            complete = result["shortlist"]["source_complete"]
        else:
            with args.snapshot.open(encoding="utf-8") as source:
                result = select_targets(json.load(source), args.min_funded_usd,
                                        include_promised=args.include_promised)
            complete = result["source_complete"]
        if args.command == "targets":
            # The existing batch preflight accepts this exact envelope. Keep
            # observation/funding evidence in the original catalog report.
            print(json.dumps({"candidates": result["targets"]}, indent=2, sort_keys=True))
            basis = (
                f" reward_basis={result['reward_basis']} minimum_reward_usd={result['minimum_reward_usd']}"
                if args.include_promised else ""
            )
            print(
                f"source_started_at={result['source_started_at']} "
                f"source_completed_at={result['source_completed_at']} "
                f"source_complete={str(complete).lower()} "
                f"candidates={len(result['targets'])}{basis}", file=sys.stderr,
            )
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        if not complete:
            basis = "funded plus promised" if args.include_promised else "funded"
            print(f"PARTIAL: retained rows do not establish a complete {basis} shortlist", file=sys.stderr)
        return 0 if complete else 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"bountyhub-catalog: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
