#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read one public BountyHub page, retaining assignment and funding semantics.

No credentials, GitHub requests, retries, claims, or submissions are performed.
Outputs are a whitelist projection, never the raw provider billing records.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

API_URL = "https://api.bountyhub.dev/api/bounties"
MAX_BYTES = 8 * 1024 * 1024
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MONEY_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
BOOL_FIELDS = ("claimed", "solved", "retracted", "isFrozen")


class IntakeError(ValueError):
    """Invalid input or an unsuccessful single fetch; safe to display."""


def money(value: Any) -> Decimal:
    """Accept nonnegative, finite USD values without rounding fractional cents."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise IntakeError("amount must be a decimal USD value")
    text = str(value)
    if len(text) > 64 or MONEY_PATTERN.fullmatch(text) is None:
        raise IntakeError("amount must be a nonnegative finite decimal USD value")
    try:
        result = Decimal(text)
        cents = result.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise IntakeError("amount is outside supported decimal precision") from exc
    if result != cents:
        raise IntakeError("amount must not contain fractional cents")
    return cents


def safe_text(value: Any, maximum: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(char for char in value[:maximum] if char.isprintable())


def catalog_url(page: int, limit: int) -> str:
    if type(page) is not int or page < 1 or type(limit) is not int or not 1 <= limit <= 50:
        raise IntakeError("page must be positive and limit must be between 1 and 50")
    return API_URL + "?" + urlencode({
        "page": page,
        "limit": limit,
        "filters": '{"solved":false}',
        "sort": '[{"orderBy":"totalAmount","order":"desc"}]',
    })


def read_bounded(stream: Any) -> bytes:
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise IntakeError("response exceeds the 8 MiB page limit; no truncated output written")
    return raw


def fetch_page(url: str) -> bytes:
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "bounty-concierge-public-intake/1",
    })
    try:
        with urlopen(request, timeout=60) as response:
            return read_bounded(response)
    except HTTPError as exc:
        try:
            retry_after = safe_text(exc.headers.get("Retry-After"), 100)
            suffix = f"; Retry-After={retry_after}" if retry_after else ""
            message = f"provider HTTP {exc.code}{suffix}; no retry performed"
        finally:
            exc.close()
        raise IntakeError(message) from exc
    except (URLError, TimeoutError, OSError) as exc:
        # Do not echo remote response bodies or environment/proxy credentials.
        raise IntakeError("public catalog request failed; no retry performed") from exc


def normalize_row(row: Any, index: int, minimum: Decimal) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(row, dict):
        return {"row_index": index, "catalog_candidate": False,
                "reconciliation_reasons": ["malformed_row"]}

    raw_id = row.get("id")
    try:
        listing_id = str(UUID(raw_id)) if isinstance(raw_id, str) else None
    except ValueError:
        listing_id = None
    if listing_id is None:
        reasons.append("invalid_listing_id")

    repo = row.get("repositoryFullName")
    issue = row.get("issueNumber")
    valid_repo = isinstance(repo, str) and len(repo) <= 250 and REPO_PATTERN.fullmatch(repo)
    valid_issue = type(issue) is int and issue > 0
    work_key = f"github:{repo.lower()}#{issue}" if valid_repo and valid_issue else None
    if work_key is None:
        reasons.append("invalid_issue_identity")

    try:
        amount = money(row.get("totalAmount"))
    except IntakeError:
        amount = None
        reasons.append("invalid_advertised_amount")
    if amount is not None and amount < minimum:
        reasons.append("below_minimum")

    issue_state = row.get("issueState")
    if issue_state != "open":
        reasons.append("issue_not_open" if issue_state == "closed" else "unknown_issue_state")
    states = {}
    for key in BOOL_FIELDS:
        value = row.get(key)
        states[key] = value if type(value) is bool else None
        if states[key] is None:
            reasons.append(f"unknown_{key}")
        elif states[key]:
            reasons.append(key)

    deleted = row.get("deletedAt") is not None if "deletedAt" in row else None
    if deleted is None:
        reasons.append("unknown_deleted_state")
    elif deleted:
        reasons.append("deleted")

    assignment = row.get("assignmentType")
    if not isinstance(assignment, str) or assignment not in {"open", "exclusive"}:
        reasons.append("unknown_assignment_type")
    assignee_value = row.get("assignee")
    if "assignee" not in row:
        has_assignee = None
        reasons.append("unknown_assignee")
    else:
        has_assignee = assignee_value is not None
        if has_assignee:
            reasons.append("assigned")
    assignee = safe_text(assignee_value.get("username"), 100) if isinstance(assignee_value, dict) else None
    payment = row.get("paymentStatus")
    if not isinstance(payment, str) or payment not in {"PAID", "PROMISED"}:
        reasons.append("unknown_funding_status")

    return {
        "row_index": index,
        "listing_id": listing_id,
        "listing_url": f"https://www.bountyhub.dev/bounty/view/{listing_id}" if listing_id else None,
        "title": safe_text(row.get("title")),
        "repository": repo if valid_repo else None,
        "issue_number": issue if valid_issue else None,
        "issue_url": f"https://github.com/{repo}/issues/{issue}" if work_key else None,
        "work_key": work_key,
        "advertised_usd": format(amount, ".2f") if amount is not None else None,
        "provider_funding_status": payment if isinstance(payment, str) and payment in {"PAID", "PROMISED"} else None,
        "assignment_type": assignment if isinstance(assignment, str) and assignment in {"open", "exclusive"} else None,
        "has_assignee": has_assignee,
        "assignee_username": assignee,
        "issue_state": issue_state if isinstance(issue_state, str) and issue_state in {"open", "closed"} else None,
        **states,
        "deleted": deleted,
        "catalog_candidate": not reasons,
        "dispatch_status": "LEAD",
        "requires_canonical_preflight": True,
        "reconciliation_reasons": reasons,
    }


def normalize(raw: bytes, minimum: Decimal, *, retrieved_at: str | None = None,
              source_url: str | None = None) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise IntakeError("input exceeds the 8 MiB page limit")
    try:
        payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (ValueError, UnicodeError) as exc:
        raise IntakeError("input is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise IntakeError("expected a catalog object containing a data list")
    if retrieved_at is not None:
        try:
            stamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone is required")
            retrieved_at = stamp.astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise IntakeError("retrieved-at must be an ISO timestamp with a timezone") from exc
    rows = [normalize_row(row, i, minimum) for i, row in enumerate(payload["data"])]
    groups: dict[str, list[str | None]] = defaultdict(list)
    for row in rows:
        if row.get("work_key"):
            groups[row["work_key"]].append(row["listing_id"])
    next_page = payload.get("hasNextPage")
    return {
        "schema": "bountyhub-public-intake/v1",
        "retrieved_at": retrieved_at,
        "normalized_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "minimum_usd": format(minimum, ".2f"),
        "coverage": "one public solved=false catalog page; not account-visible or global inventory",
        "has_next_page": next_page if type(next_page) is bool else None,
        "coverage_warnings": [] if type(next_page) is bool else ["unknown_pagination"],
        "row_count": len(rows),
        "distinct_issue_count": len(groups),
        "catalog_candidate_count": sum(row["catalog_candidate"] for row in rows),
        "interpretation": [
            "PAID is provider sponsor-funding metadata, not an award or payment to this account.",
            "PROMISED offers are retained; escrow, eligibility and actual payout require provider reconciliation.",
            "Candidates are catalog leads only. A row is never GREEN/dispatchable until canonical preflight and a fresh work-order lease authorize source mutation.",
            "Duplicate work keys share one implementation. Multiple card amounts are not summed or assumed independently claimable.",
        ],
        "duplicate_work_keys": {key: ids for key, ids in groups.items() if len(ids) > 1},
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true", help="fetch one public page once")
    source.add_argument("--input", type=Path, help="reuse an existing raw public snapshot offline")
    parser.add_argument("--output", type=Path, help="write sanitized JSON instead of stdout")
    parser.add_argument("--minimum-usd", default="15.00")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--retrieved-at", help="original retrieval time for an offline snapshot")
    args = parser.parse_args(argv)
    try:
        minimum = money(args.minimum_usd)
        if args.fetch:
            if args.retrieved_at is not None:
                raise IntakeError("retrieved-at is only accepted for offline input")
            url = catalog_url(args.page, args.limit)
            raw = fetch_page(url)
            retrieved_at = datetime.now(timezone.utc).isoformat()
        else:
            with args.input.open("rb") as stream:
                raw = read_bounded(stream)
            url = None  # An arbitrary input file does not prove a query URL.
            retrieved_at = args.retrieved_at
        result = normalize(raw, minimum, retrieved_at=retrieved_at, source_url=url)
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (IntakeError, OSError) as exc:
        print(f"bountyhub-intake: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
