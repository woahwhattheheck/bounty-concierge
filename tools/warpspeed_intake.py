#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Normalize the public warpSpeed OPEN bounty board into guarded fleet leads.

The tool performs one optional public board fetch or consumes an offline snapshot.
It never creates accounts, claims bounties, comments on GitHub, or starts paid work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BOARD_URL = (
    "https://raw.githubusercontent.com/"
    "warpspeedopen-source/warpspeed-bounties/main/BOUNTIES.md"
)
MAX_BYTES = 1024 * 1024
HEADERS = (
    "Bounty",
    "Reward",
    "Difficulty",
    "Stack",
    "Status",
    "Signup Capacity",
    "Timeline",
    "Estimated Duration",
)
MONEY_PATTERN = re.compile(r"\\$([0-9]+(?:\\.[0-9]{1,2})?)\\Z")
CAPACITY_PATTERN = re.compile(r"([0-9]{1,3})%\\s+full\\Z", re.IGNORECASE)
SEPARATOR_PATTERN = re.compile(r":?-{3,}:?\\Z")


class IntakeError(ValueError):
    """Invalid source data or a failed single fetch; safe to display."""


def money(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise IntakeError("reward must be a dollar amount string")
    match = MONEY_PATTERN.fullmatch(value.strip())
    if match is None:
        raise IntakeError("reward must look like $15 or $15.00")
    try:
        result = Decimal(match.group(1)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise IntakeError("reward is outside supported decimal precision") from exc
    return result


def minimum_money(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise IntakeError("minimum USD must be numeric")
    try:
        result = Decimal(str(value))
        cents = result.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise IntakeError("minimum USD is invalid") from exc
    if result < 0 or result != cents:
        raise IntakeError("minimum USD must be nonnegative with at most two decimals")
    return cents


def safe_text(value: Any, maximum: int = 400) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(char for char in value[:maximum] if char.isprintable())


def read_bounded(stream: Any) -> bytes:
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise IntakeError("response exceeds the 1 MiB board limit")
    return raw


def fetch_board() -> bytes:
    request = Request(
        BOARD_URL,
        headers={
            "Accept": "text/plain",
            "User-Agent": "bounty-concierge-warpspeed-intake/1",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return read_bounded(response)
    except HTTPError as exc:
        retry_after = safe_text(exc.headers.get("Retry-After"), 100)
        suffix = f"; Retry-After={retry_after}" if retry_after else ""
        raise IntakeError(f"provider HTTP {exc.code}{suffix}; no retry performed") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise IntakeError("public board request failed; no retry performed") from exc


def split_row(line: str) -> list[str]:
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        raise IntakeError("malformed markdown table row")
    return [cell.strip() for cell in line[1:-1].split("|")]


def parse_table(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        try:
            cells = split_row(line)
        except IntakeError:
            continue
        if tuple(cells) == HEADERS:
            header_index = index
            break
    if header_index is None or header_index + 1 >= len(lines):
        raise IntakeError("expected warpSpeed bounty table was not found")

    separator = split_row(lines[header_index + 1])
    if len(separator) != len(HEADERS) or any(
        SEPARATOR_PATTERN.fullmatch(cell.replace(" ", "")) is None
        for cell in separator
    ):
        raise IntakeError("bounty table separator is malformed")

    rows: list[dict[str, str]] = []
    for line in lines[header_index + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        cells = split_row(line)
        if len(cells) != len(HEADERS):
            raise IntakeError("bounty table row has an unexpected column count")
        rows.append(dict(zip(HEADERS, cells)))
    if not rows:
        raise IntakeError("bounty table contains no rows")
    return rows


def parse_capacity(value: str) -> int:
    match = CAPACITY_PATTERN.fullmatch(value.strip())
    if match is None:
        raise IntakeError("signup capacity must look like '66% full'")
    result = int(match.group(1))
    if result > 100:
        raise IntakeError("signup capacity must be between 0% and 100%")
    return result


def normalize_row(row: dict[str, str], index: int, minimum: Decimal) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        reward = money(row["Reward"])
    except IntakeError:
        reward = None
        reasons.append("invalid_advertised_reward")
    if reward is not None and reward < minimum:
        reasons.append("below_minimum")

    status = row["Status"].strip()
    if status.lower() != "open":
        reasons.append("board_status_not_open")

    try:
        capacity = parse_capacity(row["Signup Capacity"])
    except IntakeError:
        capacity = None
        reasons.append("unknown_signup_capacity")
    if capacity == 100:
        reasons.append("signup_capacity_full")

    candidate = not reasons
    return {
        "row_index": index,
        "title": safe_text(row["Bounty"]),
        "advertised_usd": format(reward, ".2f") if reward is not None else None,
        "difficulty": safe_text(row["Difficulty"]),
        "stack": safe_text(row["Stack"]),
        "board_status": status if status else None,
        "signup_capacity_percent_full": capacity,
        "timeline": safe_text(row["Timeline"]),
        "estimated_duration": safe_text(row["Estimated Duration"]),
        "catalog_candidate": candidate,
        "dispatch_status": "CLAIM_GATE" if candidate else "PRUNE",
        "build_allowed": False,
        "provider_funding_status": "ADVERTISED",
        "sponsor_verification": "UNVERIFIED",
        "requires_account_signup": True,
        "requires_maintainer_confirmation": True,
        "requires_canonical_preflight": True,
        "next_action": (
            "VERIFY_ACCOUNT_AND_CURRENT_TERMS_THEN_REQUEST_MAINTAINER_ASSIGNMENT"
            if candidate
            else None
        ),
        "reconciliation_reasons": reasons,
    }


def normalize(
    raw: bytes,
    minimum: Decimal,
    *,
    retrieved_at: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise IntakeError("input exceeds the 1 MiB board limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise IntakeError("input is not valid UTF-8") from exc

    if retrieved_at is not None:
        try:
            stamp = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timezone required")
            retrieved_at = stamp.astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise IntakeError("retrieved-at must be an ISO timestamp with a timezone") from exc

    raw_rows = parse_table(text)
    rows = [normalize_row(row, index, minimum) for index, row in enumerate(raw_rows)]
    return {
        "schema": "warpspeed-public-intake/v1",
        "retrieved_at": retrieved_at,
        "normalized_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "minimum_usd": format(minimum, ".2f"),
        "coverage": "public BOUNTIES.md snapshot only; not account, signup, assignment, issue, or payout state",
        "row_count": len(rows),
        "catalog_candidate_count": sum(bool(row["catalog_candidate"]) for row in rows),
        "interpretation": [
            "The dollar amount is advertised reward metadata, not proof of escrow, sponsor verification, award, invoice, or payment.",
            "A catalog candidate is not build-authorized. warpSpeed requires developer signup, a claim request, and maintainer confirmation before paid work begins.",
            "Rows at 100% signup capacity are pruned. Lower capacity is only a lead and still requires current official-page, canonical-issue, and fleet-ownership preflight.",
            "Timeline text is preserved but not treated as authoritative eligibility when the public board still marks a row Open; reconcile current terms before claiming.",
            "This tool performs no account, GitHub comment, claim, code, submission, or payout mutation.",
        ],
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true", help="fetch the public board once")
    source.add_argument("--input", type=Path, help="reuse a saved BOUNTIES.md snapshot offline")
    parser.add_argument("--output", type=Path, help="write sanitized JSON instead of stdout")
    parser.add_argument("--minimum-usd", default="15.00")
    parser.add_argument("--retrieved-at", help="original retrieval time for offline input")
    args = parser.parse_args(argv)

    try:
        minimum = minimum_money(args.minimum_usd)
        if args.fetch:
            if args.retrieved_at is not None:
                raise IntakeError("retrieved-at is only accepted for offline input")
            raw = fetch_board()
            source_url = BOARD_URL
            retrieved_at = datetime.now(timezone.utc).isoformat()
        else:
            with args.input.open("rb") as stream:
                raw = read_bounded(stream)
            source_url = None
            retrieved_at = args.retrieved_at

        result = normalize(
            raw,
            minimum,
            retrieved_at=retrieved_at,
            source_url=source_url,
        )
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (IntakeError, OSError) as exc:
        print(f"warpspeed-intake: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
